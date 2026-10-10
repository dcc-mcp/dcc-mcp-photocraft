"""PhotoCraft MCP server composition and lifecycle."""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Any, Optional

from dcc_mcp_core import AdapterReadinessBinder, DccServerOptions, HostExecutionBridge
from dcc_mcp_core.host import QueueDispatcher, StandaloneHost
from dcc_mcp_core.server_base import DccServerBase

from .__version__ import __version__
from .actions import register_actions
from .control_channel import ControlChannel, ControlChannelUnavailable, endpoint_from_env
from .env import (
    ENV_CLI,
    ENV_CONTROL_PORT,
    ENV_READ_ROOT,
    ENV_WRITE_ROOT,
)
from .host import discover, find_binary

# PhotoCraft is driven from an external process, so the adapter owns no port of
# its own by default and lets Core pick one.
DEFAULT_PORT = 0


class PhotoCraftMcpServer(DccServerBase):
    """DCC-MCP server that drives PhotoCraft as an external process.

    PhotoCraft has no embedded Python, so unlike the Maya or Blender adapters this
    server never runs inside the host. It composes two host surfaces instead:

    * a headless ``photocraft-cli`` subprocess, which is the primary path and the
      only one available in CI;
    * the desktop app's loopback control channel, which is used when a running
      instance was started with ``--control <port>``.

    Both are optional at construction time: a machine that only has the CLI
    installed still gets every headless capability, and the absence of the
    desktop app is reported as readiness rather than raised as a startup
    failure.
    """

    def __init__(
        self,
        port: Optional[int] = None,
        *,
        gateway_port: Optional[int] = None,
        registry_dir: Optional[str] = None,
        enable_gateway_failover: bool = True,
        strict_gateway: bool = False,
        enable_file_logging: bool = True,
        enable_job_persistence: bool = True,
        enable_telemetry: bool = True,
        enable_checkpoint_persistence: bool = True,
        enable_checkpoint_tools: bool = True,
        job_retention_hours: Optional[int] = None,
        checkpoint_path: Optional[str] = None,
        cli_binary: Optional[str] = None,
        read_root: Optional[str] = None,
        write_root: Optional[str] = None,
    ) -> None:
        self._host_dispatcher = QueueDispatcher()
        self._host_driver = StandaloneHost(self._host_dispatcher, thread_name="dcc-mcp-photocraft-host")
        execution_bridge = HostExecutionBridge(
            dispatcher=self._host_dispatcher,
            host_dispatcher=self._host_dispatcher,
            default_thread_affinity="any",
            default_execution="sync",
            default_timeout_hint_secs=60,
        )
        options = DccServerOptions.from_env(
            "photocraft",
            Path(__file__).resolve().parent / "skills",
            port=DEFAULT_PORT if port is None else port,
            server_name="dcc-mcp-photocraft",
            server_version=__version__,
            execution_bridge=execution_bridge,
            gateway_port=gateway_port,
            registry_dir=registry_dir,
            enable_gateway_failover=enable_gateway_failover,
            strict_gateway=strict_gateway,
            enable_file_logging=enable_file_logging,
            enable_job_persistence=enable_job_persistence,
            enable_telemetry=enable_telemetry,
            enable_checkpoint_persistence=enable_checkpoint_persistence,
            enable_checkpoint_tools=enable_checkpoint_tools,
            job_retention_hours=job_retention_hours,
            checkpoint_path=checkpoint_path,
        )
        super().__init__(options)

        self._cli_binary = cli_binary or os.environ.get(ENV_CLI)
        self._read_root = read_root or os.environ.get(ENV_READ_ROOT)
        self._write_root = write_root or os.environ.get(ENV_WRITE_ROOT)
        self._readiness = AdapterReadinessBinder(self, dcc_ready_probe=self._host_ready)
        register_actions(self)

    # -- host surfaces -----------------------------------------------------

    def _host_ready(self) -> bool:
        """Report whether any PhotoCraft entry point is usable right now."""
        if self._cli_binary:
            return Path(self._cli_binary).is_file()
        return find_binary("photocraft-cli") is not None

    def control_channel(self) -> ControlChannel:
        """Build a control channel to the running PhotoCraft instance.

        The endpoint is rebuilt on every call so that a change to
        ``PHOTOCRAFT_CONTROL_PORT`` or to the token file takes effect without a
        server restart. This costs nothing: ``ControlChannel.call`` already opens
        a fresh connection and authenticates per request, so the only thing
        rebuilt here is a port/token pair.
        """
        endpoint = endpoint_from_env()
        if endpoint is None:
            raise ControlChannelUnavailable(
                f"no control port configured; start PhotoCraft with --control <port> and set {ENV_CONTROL_PORT}"
            )
        return ControlChannel(endpoint)

    @property
    def read_root(self) -> Optional[str]:
        return self._read_root

    @property
    def write_root(self) -> Optional[str]:
        return self._write_root

    @property
    def cli_binary(self) -> Optional[str]:
        return self._cli_binary

    def host_status(self) -> dict[str, Any]:
        """Describe the detected host binaries for diagnostics."""
        binaries = discover()
        return {
            "cli": str(binaries.cli) if binaries.cli else None,
            "app": str(binaries.app) if binaries.app else None,
            "configured_cli": self._cli_binary,
            "control_port": os.environ.get(ENV_CONTROL_PORT),
            "read_root": self._read_root,
            "write_root": self._write_root,
        }

    # -- lifecycle ---------------------------------------------------------

    def start(self, **kwargs: Any) -> Any:
        self._host_driver.start()
        try:
            self._readiness.bind_queue_dispatcher(self._host_dispatcher)
            self._readiness.mark_execution_ready()
            self._readiness.mark_dispatcher_ready()
            self._readiness.refresh_dcc_ready()
            self._readiness.publish()
            return super().start(**kwargs)
        except BaseException:
            # A failed start must not leave the host thread running: the driver
            # outlives the exception otherwise, and a later start would run
            # against a readiness state that was never published.
            self._host_driver.stop()
            raise

    def stop(self, **kwargs: Any) -> Any:
        try:
            return super().stop(**kwargs)
        finally:
            self._host_driver.stop()


_server: Optional[PhotoCraftMcpServer] = None


def start_server(**kwargs: Any) -> PhotoCraftMcpServer:
    """Start the process-wide PhotoCraft MCP server.

    The global is assigned only after ``start()`` returns, so a server that
    failed to start is never cached: keeping it would make every later call
    return a dead instance that ``capability_dispatch`` still treats as running.
    """
    global _server
    if _server is None:
        server = PhotoCraftMcpServer(**kwargs)
        server.start()
        _server = server
    return _server


def stop_server() -> None:
    """Stop the process-wide server, if one is running."""
    global _server
    if _server is not None:
        _server.stop()
        _server = None


def main() -> None:
    """Run the standalone adapter until interrupted."""
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    start_server()
    try:
        stopped.wait()
    finally:
        stop_server()
