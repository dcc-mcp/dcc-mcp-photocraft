"""Loopback JSON-lines client for a running PhotoCraft instance.

PhotoCraft exposes two automation surfaces that a Python adapter can drive
without a Rust toolchain:

* ``photocraft-cli`` -- a headless subprocess. ``photocraft-cli serve`` keeps one
  engine session and speaks the same JSON-lines envelope on stdio, and
  ``photocraft-cli mcp`` wraps it as an MCP server. No window is needed.
* ``photocraft --control <port>`` -- a control channel that drives the *running*
  desktop app.

This module implements the second surface. It is deliberately dependency-free:
the wire format is newline-delimited JSON over a loopback socket, so the
standard library is enough. Keeping it free of a third-party MCP SDK means the
adapter does not inherit that SDK's Python floor or its release cadence, and it
does not have to track upstream protocol revisions just to forward a line of
JSON.

Two things differ from the sibling PdfCraft adapter, and both are easy to copy
wrong:

* **The port is configured, not discovered.** PdfCraft writes its port into a
  control file on each launch; PhotoCraft binds the port given to
  ``--control <port>`` (or ``PHOTOCRAFT_CONTROL_PORT``). There is no file to
  re-read, so the port comes from the environment. Because the port is chosen by
  whoever launches the app rather than by the app, the operator is responsible
  for not colliding with the sibling Craft apps, all of which listen on
  loopback.
* **The auth frame is still required.** Upstream's control protocol makes an
  ``auth`` frame the mandatory first request on every connection, and it refuses
  to dispatch anything before that frame succeeds. Any token or token file is
  optional at *launch* -- upstream generates one and writes it to stderr when
  none is given -- so the port alone is never sufficient.

The reply envelope also differs: PhotoCraft answers ``{"ok": false, "error":
"..."}`` rather than a JSON-RPC 2.0 error object, so errors are read from the
``ok`` flag.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# PhotoCraft's control server enforces a 30-second socket read/write timeout.
# Mirroring the bound here keeps a stalled app from hanging the adapter
# indefinitely.
DEFAULT_TIMEOUT_SECS = 30.0

HOST = "127.0.0.1"


class ControlChannelError(RuntimeError):
    """A control-channel request could not be completed."""


class ControlChannelUnavailable(ControlChannelError):
    """The control channel is absent, unreachable, or not authenticated."""


@dataclass(frozen=True)
class Endpoint:
    """The loopback port and bearer token for one PhotoCraft instance."""

    port: int
    token: str


def _parse_port(value: Any) -> int:
    """Validate a configured port, rejecting rather than truncating.

    A port above the valid range would silently reach some other service, and a
    port of 0 asks the OS to choose -- which is meaningless for a channel we
    have to connect *to*, so it is refused here with a message that says why.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlChannelUnavailable(f"control port must be a number, got {value!r}")
    if not 0 < value < 65536:
        raise ControlChannelUnavailable(f"control port {value} is out of range; expected 1-65535")
    return value


def read_token(token: str | None = None, token_file: str | Path | None = None) -> str:
    """Resolve the bearer token from a literal value or a token file.

    Upstream accepts either form and prefers the file, because a token on the
    command line is visible to every process that can read the process table.
    """
    if token:
        return token
    if token_file:
        path = Path(token_file)
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ControlChannelUnavailable(f"cannot read control token file {path}: {exc}") from exc
        if not raw:
            raise ControlChannelUnavailable(f"control token file {path} is empty")
        return raw
    raise ControlChannelUnavailable(
        "no control token configured; set PHOTOCRAFT_CONTROL_TOKEN or PHOTOCRAFT_CONTROL_TOKEN_FILE"
    )


def read_endpoint(
    port: Any,
    token: str | None = None,
    token_file: str | Path | None = None,
) -> Endpoint:
    """Build an endpoint from a configured port and token.

    Unlike the sibling PdfCraft adapter there is no control file to parse: the
    port is the one passed to ``photocraft --control <port>``, so a missing or
    malformed port means the environment was not set up rather than that the app
    is not running.
    """
    if isinstance(port, str):
        text = port.strip()
        if not text:
            raise ControlChannelUnavailable("control port is empty")
        try:
            port = int(text)
        except ValueError as exc:
            raise ControlChannelUnavailable(f"control port {port!r} is not a number") from exc

    return Endpoint(port=_parse_port(port), token=read_token(token, token_file))


def endpoint_from_env(environ: dict[str, str] | None = None) -> Endpoint | None:
    """Build an endpoint from the environment, or ``None`` when unconfigured.

    ``None`` is a normal state, not an error: the control channel is needed only
    for actions that drive the live UI, and a machine that runs headless work
    through the CLI is usable without it.
    """
    env = os.environ if environ is None else environ
    raw_port = env.get("PHOTOCRAFT_CONTROL_PORT")
    if raw_port is None or not raw_port.strip():
        return None
    return read_endpoint(
        raw_port,
        env.get("PHOTOCRAFT_CONTROL_TOKEN"),
        env.get("PHOTOCRAFT_CONTROL_TOKEN_FILE"),
    )


class ControlChannel:
    """A newline-delimited JSON client for one PhotoCraft instance.

    Connections are opened lazily per request and authenticated on connect.
    PhotoCraft spawns a thread per connection and answers requests sequentially,
    so a short-lived connection per call is the simplest correct shape and avoids
    holding a slot in its 16-connection limit between calls.
    """

    def __init__(self, endpoint: Endpoint, *, timeout_secs: float = DEFAULT_TIMEOUT_SECS) -> None:
        self._endpoint = endpoint
        self._timeout_secs = timeout_secs
        self._request_id = 0

    @property
    def port(self) -> int:
        return self._endpoint.port

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one request and return its ``result``, or raise on ``error``."""
        self._request_id += 1
        request_id = self._request_id

        try:
            with socket.create_connection((HOST, self._endpoint.port), timeout=self._timeout_secs) as connection:
                # ``makefile`` is used instead of ``recv`` so that a reply split
                # across TCP segments is still read as one line.
                stream = connection.makefile("rb")
                try:
                    # The first frame on every connection must be the auth
                    # request; upstream refuses to dispatch anything else first.
                    self._send(connection, "auth", {"token": self._endpoint.token}, request_id="auth")
                    self._receive(stream, request_id="auth")
                    self._send(connection, method, params if params is not None else {}, request_id=request_id)
                    return self._receive(stream, request_id=request_id)
                finally:
                    stream.close()
        except OSError as exc:
            raise ControlChannelUnavailable(f"cannot reach PhotoCraft on {HOST}:{self._endpoint.port}: {exc}") from exc

    @staticmethod
    def _send(
        connection: socket.socket,
        method: str,
        params: dict[str, Any],
        *,
        request_id: Any,
    ) -> None:
        payload = json.dumps({"id": request_id, "method": method, "params": params})
        connection.sendall(payload.encode("utf-8") + b"\n")

    def _receive(self, stream: Any, *, request_id: Any) -> Any:
        line = stream.readline()
        if not line:
            raise ControlChannelUnavailable("PhotoCraft closed the connection without answering")
        try:
            reply = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControlChannelError(f"PhotoCraft sent a malformed reply: {exc}") from exc

        if not isinstance(reply, dict):
            raise ControlChannelError(f"PhotoCraft sent a non-object reply: {reply!r}")

        # PhotoCraft answers with ``{"ok": false, "error": "..."}`` rather than a
        # JSON-RPC 2.0 error object, so the failure flag is ``ok``.
        if not reply.get("ok", False):
            message = reply.get("error")
            if not isinstance(message, str) or not message:
                message = "unknown error"
            raise ControlChannelError(f"{message}")

        if reply.get("id") != request_id:
            raise ControlChannelError(f"reply id {reply.get('id')!r} does not match request id {request_id!r}")
        return reply.get("result")
