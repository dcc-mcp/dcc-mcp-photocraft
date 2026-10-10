"""Headless driving of PhotoCraft through its ``photocraft-cli`` subprocess.

This is the primary automation path: it needs no window, no display server and
no control channel, and it is the only path that works in CI.

The command set is discovered at runtime rather than declared here, because
upstream ships new minor versions continuously and grows the command table with
them. ``photocraft-cli`` publishes that table, so a hardcoded list would be stale
on the next upstream release.

PhotoCraft differs from the sibling PdfCraft adapter in the root flags: it takes
the read and write authorities separately as ``--automation-read-root`` and
``--automation-write-root``, and upstream fails closed unless the applicable root
was granted at launch. Paths handed to the CLI are interpreted relative to those
roots, so forwarding them is what makes file access work at all.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .host import CLI_BINARY, find_binary

DEFAULT_TIMEOUT_SECS = 120.0


class CliError(RuntimeError):
    """A ``photocraft-cli`` invocation failed."""


class CliNotFound(CliError):
    """The ``photocraft-cli`` binary could not be located."""


class CliTransport:
    """Run ``photocraft-cli`` subcommands and parse their JSON output."""

    def __init__(
        self,
        binary: str | Path | None = None,
        *,
        read_root: str | Path | None = None,
        write_root: str | Path | None = None,
        timeout_secs: float = DEFAULT_TIMEOUT_SECS,
    ) -> None:
        resolved = Path(binary) if binary is not None else find_binary(CLI_BINARY)
        # An explicitly configured path is checked too: a stale PHOTOCRAFT_CLI
        # should fail here with a clear message rather than surfacing as an
        # opaque subprocess spawn error on the first tool call.
        if resolved is None or not resolved.is_file():
            raise CliNotFound("photocraft-cli was not found; install PhotoCraft or set PHOTOCRAFT_CLI to its path")
        self._binary = str(resolved)
        self._read_root = str(read_root) if read_root is not None else None
        self._write_root = str(write_root) if write_root is not None else None
        self._timeout_secs = timeout_secs

    @property
    def binary(self) -> str:
        return self._binary

    @property
    def read_root(self) -> str | None:
        return self._read_root

    @property
    def write_root(self) -> str | None:
        return self._write_root

    def _base_args(self) -> list[str]:
        args = [self._binary]
        # The two roots are independent authorities rather than one sandbox:
        # upstream refuses a read without a read root and a write without a
        # write root, so each is forwarded only when it was actually granted.
        if self._read_root:
            args += ["--automation-read-root", self._read_root]
        if self._write_root:
            args += ["--automation-write-root", self._write_root]
        return args

    def run(self, args: list[str], *, timeout_secs: float | None = None) -> str:
        """Run one subcommand and return its stdout, raising on a non-zero exit."""
        command = self._base_args() + list(args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_secs if timeout_secs is None else timeout_secs,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Report the bound that actually applied: a per-call override
            # replaces the instance default, and quoting the default would
            # misreport why the call failed.
            applied = self._timeout_secs if timeout_secs is None else timeout_secs
            raise CliError(f"{' '.join(command)} timed out after {applied}s") from exc
        except OSError as exc:
            raise CliError(f"cannot run {self._binary}: {exc}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise CliError(f"{' '.join(command)} failed with exit {completed.returncode}: {detail}")
        return completed.stdout

    def methods(self) -> list[Any]:
        """Return the headless session's method list.

        ``photocraft-cli serve`` answers a ``methods`` request over its JSON-lines
        stdio channel with the method names it accepts. That channel is a
        long-lived session rather than a one-shot subcommand, so the request is
        posted through :meth:`_serve_lines` and the connection is closed
        immediately afterwards: one request per invocation keeps the session from
        outliving the call.

        The result is a list of method names rather than a table of objects,
        which is what upstream returns; it is read from the host rather than
        mirrored here for the same reason as the command table.
        """
        payload = self._serve_lines({"id": 1, "method": "methods", "params": {}})
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            methods = payload.get("methods")
            if isinstance(methods, list):
                return methods
        raise CliError("photocraft-cli serve methods returned an unexpected payload shape")

    def _serve_lines(self, request: dict[str, Any]) -> Any:
        """Post one JSON-lines request to ``photocraft-cli serve`` over stdio.

        ``serve`` is the one surface that is a session rather than a subcommand,
        so it cannot be driven through :meth:`run`: the request has to be written
        to the process's stdin and the reply read from its stdout.
        """
        command = self._base_args() + ["serve"]
        payload = json.dumps(request) + "\n"
        try:
            completed = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                text=True,
                timeout=self._timeout_secs,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CliError(f"{' '.join(command)} timed out after {self._timeout_secs}s") from exc
        except OSError as exc:
            raise CliError(f"cannot run {self._binary}: {exc}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise CliError(f"{' '.join(command)} failed with exit {completed.returncode}: {detail}")

        reply = _parse_json(completed.stdout, "serve")
        if not isinstance(reply, dict):
            raise CliError("photocraft-cli serve sent a non-object reply")
        # ``serve`` answers with the same envelope as the control channel: an
        # ``ok`` flag rather than a JSON-RPC error object.
        if not reply.get("ok", False):
            raise CliError(f"photocraft-cli serve reported: {reply.get('error') or 'unknown error'}")
        if reply.get("id") != request["id"]:
            raise CliError(f"reply id {reply.get('id')!r} does not match request id {request['id']!r}")
        return reply.get("result")

    def commands(self) -> list[dict[str, Any]]:
        """Return the engine command table, which the CLI publishes as JSON."""
        output = self.run(["commands", "--json"])
        payload = _parse_json(output, "commands")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            commands = payload.get("commands")
            if isinstance(commands, list):
                return [item for item in commands if isinstance(item, dict)]
        return []

    def run_command(self, command_id: str, params: dict[str, Any] | None = None) -> Any:
        """Run one engine command by id through ``photocraft-cli run``.

        Command ids are resolved against the table upstream publishes, so this
        accepts any id the installed host reports rather than a local allowlist.
        """
        args = ["--cmd", command_id]
        if params:
            args += ["--params", json.dumps(params)]
        return _parse_json(self.run(["run", "--new", "{}", *args]), f"run {command_id}")

    def info(self, document: str | Path) -> dict[str, Any]:
        """Return the JSON summary of one PhotoCraft document (``.pcraft``)."""
        payload = _parse_json(self.run(["info", str(document)]), "info")
        return payload if isinstance(payload, dict) else {"result": payload}


def _encode_value(value: Any) -> str:
    """Render one argument value the way the CLI expects."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json(output: str, what: str) -> Any:
    text = output.strip()
    if not text:
        raise CliError(f"photocraft-cli {what} produced no output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Upstream writes progress or warnings around the JSON payload in some
        # subcommands, so fall back to the last line that parses as JSON.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise CliError(f"photocraft-cli {what} produced no JSON")
