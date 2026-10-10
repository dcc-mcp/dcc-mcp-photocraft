"""Adapter-owned MCP actions that are not part of the PhotoCraft command table.

The shipped skill catalog carries the PhotoCraft-owned actions. The actions
registered here are the adapter's own: they report what the host exposes and
how healthy it is, and they are the ones an agent calls first to decide what
else it can do.
"""

from __future__ import annotations

from typing import Any

from .capability_dispatch import ActionError, dispatch
from .host import probe


def _envelope(result: Any) -> dict[str, Any]:
    return {"ok": True, "result": result}


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


def host_status() -> dict[str, Any]:
    """Report the detected PhotoCraft binaries and version.

    Safe to call when PhotoCraft is absent: a missing host is a reportable state,
    not a failure, so an agent can distinguish "not installed" from "broken".
    """
    status = probe()
    return _envelope({"available": status.cli_available, "detail": status.describe()})


def list_photocraft_commands() -> dict[str, Any]:
    """List the engine command table that the installed PhotoCraft publishes.

    The list is read from the host at call time. Upstream grows this table
    continuously, so a cached or hardcoded copy would be stale on the next
    release.
    """
    try:
        return _envelope(dispatch("list_commands"))
    except ActionError as exc:
        return _error(exc)


def list_photocraft_methods() -> dict[str, Any]:
    """List the headless session methods that the installed PhotoCraft publishes.

    ``photocraft-cli serve`` keeps one headless engine session and answers a
    ``methods`` request with the names it accepts. The list is read from the host
    at call time for the same reason as the command table.
    """
    try:
        return _envelope(dispatch("list_methods"))
    except ActionError as exc:
        return _error(exc)


def document_info(path: str) -> dict[str, Any]:
    """Return the JSON summary of one PhotoCraft document (``.pcraft``)."""
    try:
        return _envelope(dispatch("document_info", {"path": path}))
    except ActionError as exc:
        return _error(exc)


ADAPTER_ACTIONS = {
    "host_status": host_status,
    "list_photocraft_commands": list_photocraft_commands,
    "list_photocraft_methods": list_photocraft_methods,
    "document_info": document_info,
}


def register_actions(server: Any) -> None:
    """Register the adapter-owned actions on a server instance.

    The dict is exposed as attributes so Core's skill machinery and tests can
    reach them by name. Registration is additive: any failure to register one
    action must not prevent the rest from being served, so each is attached in
    its own step.
    """
    for name, func in ADAPTER_ACTIONS.items():
        setattr(server, name, func)
