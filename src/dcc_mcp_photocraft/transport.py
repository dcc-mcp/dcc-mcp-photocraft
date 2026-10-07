"""One owned official MCP stdio process; no JSON-RPC implementation or retries."""

import asyncio
import concurrent.futures
import json
import os
import re
import subprocess
import threading
from datetime import timedelta
from pathlib import Path

import anyio
from anyio.abc import ObjectReceiveStream
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import PHOTOCRAFT_VERSION
from .errors import AdapterError
from .paths import Workspace

REQUIRED_TOOLS = {
    "session_list",
    "doc_new",
    "doc_open",
    "doc_save",
    "doc_export",
    "doc_inspect",
    "doc_render_preview",
    "doc_select",
    "doc_close",
    "command_list",
    "command_run",
}
REQUIRED_PROPERTIES = {
    "doc_open": {"path"},
    "doc_new": {"width", "height", "background", "name"},
    "doc_save": {"path"},
    "doc_export": {"path"},
    "doc_render_preview": {"max_side"},
    "doc_select": {"index"},
    "command_run": {"id", "params"},
}


class _ObserveEof(ObjectReceiveStream):
    """Observe SDK transport lifetime without replacing its protocol parser."""

    def __init__(self, stream, disconnected):
        self.stream = stream
        self.disconnected = disconnected

    async def receive(self):
        try:
            return await self.stream.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            self.disconnected()
            raise

    async def aclose(self):
        await self.stream.aclose()


class ManagedPhotoCraft:
    @property
    def _state(self):
        return self._connection_state

    @_state.setter
    def _state(self, value):
        self._connection_state = value
        if self.on_state_change is not None:
            self.on_state_change()

    def __init__(
        self,
        executable: Path,
        workspace: Workspace,
        timeout_seconds: float = 30,
        *,
        executable_args: list[str] | None = None,
    ):
        self.executable = Path(executable).resolve(strict=True)
        self.workspace = workspace
        if not 0 < timeout_seconds <= 120:
            raise AdapterError("invalid_timeout")
        self.timeout_seconds = timeout_seconds
        self._executable_args = list(executable_args or [])
        self.on_state_change = None
        self._state = "new"
        self._version = None
        self._thread = None
        self._loop = None
        self._queue = None
        self._lock = threading.RLock()
        self._ready = concurrent.futures.Future()

    def status(self) -> dict:
        return {
            "state": self._state,
            "photocraft_version": self._version,
            "mode": "headless",
            "managed": True,
            "desktop_bridge": False,
            "upstream_jobs": False,
            "automatic_replay": False,
        }

    def start(self) -> dict:
        with self._lock:
            if self._state == "connected":
                return self.status()
            if self._state != "new":
                raise AdapterError("connection_unavailable")
            try:
                probe = subprocess.run(
                    [str(self.executable), *self._executable_args, "--version"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=max(5, self.timeout_seconds),
                    check=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                match = re.fullmatch(
                    r"photocraft-cli (\d+\.\d+\.\d+)(?: \([^\r\n]+\))?\s*", probe.stdout
                )
                if not match or match[1] != PHOTOCRAFT_VERSION:
                    raise AdapterError("unsupported_version")
                self._version = match[1]
                self._state = "starting"
                self._thread = threading.Thread(
                    target=self._run, name="photocraft-mcp", daemon=True
                )
                self._thread.start()
                self._ready.result(timeout=self.timeout_seconds + 15)
                return self.status()
            except AdapterError:
                self._state = "poisoned"
                raise
            except Exception:
                self._state = "poisoned"
                raise AdapterError("connection_failed") from None

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            self._state = "poisoned"
            if not self._ready.done():
                self._ready.set_exception(AdapterError("connection_failed"))

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=1)
        protocol_error = asyncio.Event()

        async def observe_message(message):
            # The official SDK reports malformed frames and unknown response
            # IDs through this public hook. A later valid ID cannot erase it.
            if isinstance(message, Exception):
                protocol_error.set()
                disconnected()

        async def checked(awaitable):
            operation = asyncio.create_task(awaitable)
            failed = asyncio.create_task(protocol_error.wait())
            try:
                await asyncio.wait((operation, failed), return_when=asyncio.FIRST_COMPLETED)
                if protocol_error.is_set():
                    raise AdapterError("transport_desync")
                return await operation
            finally:
                for task in (operation, failed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(operation, failed, return_exceptions=True)

        def disconnected():
            if self._state != "closing":
                self._state = "poisoned"
            if self._queue.empty():
                self._queue.put_nowait(None)

        # The original input root is never passed to PhotoCraft. It can only
        # read staged inputs and generated outputs in its isolated workspace.
        root = str(self.workspace.output_root)
        env = {
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG"}
        }
        env["PHOTOCRAFT_CONFIG_DIR"] = str(self.workspace.output_root / ".config")
        params = StdioServerParameters(
            command=str(self.executable),
            args=[
                *self._executable_args,
                "mcp",
                "--automation-read-root",
                root,
                "--automation-write-root",
                root,
            ],
            env=env,
            cwd=root,
        )
        # Upstream diagnostics may contain local paths. Keep them out of Core
        # tool output and public logs; the official SDK owns process cleanup.
        with open(os.devnull, "w") as diagnostics:
            async with stdio_client(params, errlog=diagnostics) as (read, write):
                async with ClientSession(
                    _ObserveEof(read, disconnected),
                    write,
                    read_timeout_seconds=timedelta(seconds=max(5, self.timeout_seconds)),
                    message_handler=observe_message,
                ) as session:
                    async with asyncio.timeout(max(5, self.timeout_seconds)):
                        initialized = await checked(session.initialize())
                        catalog = await checked(session.list_tools())
                    if (
                        initialized.serverInfo.name != "photocraft"
                        or initialized.serverInfo.version != PHOTOCRAFT_VERSION
                    ):
                        self._ready.set_exception(AdapterError("unsupported_version"))
                        self._state = "poisoned"
                        return
                    if initialized.capabilities.tools is None:
                        self._ready.set_exception(AdapterError("missing_capability"))
                        self._state = "poisoned"
                        return
                    indexed = {tool.name: tool for tool in catalog.tools}
                    if not REQUIRED_TOOLS.issubset(indexed):
                        self._ready.set_exception(AdapterError("missing_capability"))
                        self._state = "poisoned"
                        return
                    schemas = json.loads(
                        Path(__file__).with_name("upstream_schemas.json").read_text()
                    )
                    for name, schema in schemas.items():
                        if indexed[name].inputSchema != schema:
                            self._ready.set_exception(AdapterError("incompatible_schema"))
                            self._state = "poisoned"
                            return
                    self._state = "connected"
                    self._ready.set_result(True)
                    while True:
                        item = await self._queue.get()
                        if item is None:
                            return
                        name, arguments, mutating, meta, future = item
                        try:
                            async with asyncio.timeout(self.timeout_seconds):
                                result = await checked(
                                    session.call_tool(name, arguments, meta=meta)
                                )
                            dumped = result.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                            if len(json.dumps(dumped)) > 24 * 1024 * 1024:
                                raise AdapterError("response_limit", indeterminate=mutating)
                            future.set_result(dumped)
                        except AdapterError as error:
                            # A valid but over-budget result is not a framing
                            # failure. Never discard unsaved state for it.
                            if error.code == "response_limit":
                                future.set_exception(error)
                            else:
                                self._state = "poisoned"
                                future.set_exception(
                                    AdapterError(error.code, indeterminate=mutating)
                                )
                                return
                        except BaseException:
                            # No retry, reconnect, or replacement process. Closing
                            # stdio terminates the owned process before reuse.
                            self._state = "poisoned"
                            if not future.done():
                                future.set_exception(
                                    AdapterError("transport_error", indeterminate=mutating)
                                )
                            return

    def call(self, name: str, arguments: dict, mutating: bool = False, *, meta=None) -> dict:
        with self._lock:
            if self._state != "connected":
                raise AdapterError("connection_unavailable")
            if name not in REQUIRED_TOOLS:
                raise AdapterError("unsupported_tool")
            if len(json.dumps(arguments, allow_nan=False)) > 32 * 1024:
                raise AdapterError("request_limit")
            future = concurrent.futures.Future()
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, (name, arguments, mutating, meta, future)
            )
            try:
                return future.result(timeout=self.timeout_seconds + 2)
            except concurrent.futures.TimeoutError:
                self._state = "poisoned"
                raise AdapterError("transport_error", indeterminate=mutating) from None

    def close(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._state == "connected":
                    self._state = "closing"
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
                self._thread.join(timeout=self.timeout_seconds + 15)
                if self._thread.is_alive():
                    raise AdapterError("shutdown_incomplete", indeterminate=True)
            if self._state != "poisoned":
                self._state = "closed"
