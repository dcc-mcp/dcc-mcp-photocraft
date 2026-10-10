"""Self-check for the PhotoCraft adapter.

The doctor answers one question an agent cannot answer any other way: is this
machine actually able to drive PhotoCraft right now, and if not, which of the two
host surfaces is missing? Reporting the two separately matters because they
enable different things -- the CLI alone supports every headless capability,
while the desktop app adds the UI-driven ones -- so a machine with only the CLI
installed is usable, not broken.

Every check is read-only. Nothing here starts the app or opens a port.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .control_channel import ControlChannelUnavailable, endpoint_from_env
from .host import APP_BINARY, CLI_BINARY, probe

# Environment variables the adapter honours, reported so a stale setting is
# visible next to the binaries it overrides.
ENV_VARS = (
    "PHOTOCRAFT_CLI",
    "PHOTOCRAFT_APP",
    "PHOTOCRAFT_CONTROL_PORT",
    "PHOTOCRAFT_CONTROL_TOKEN",
    "PHOTOCRAFT_CONTROL_TOKEN_FILE",
    "PHOTOCRAFT_READ_ROOT",
    "PHOTOCRAFT_WRITE_ROOT",
)


def _check_cli(status: Any) -> dict[str, Any]:
    if status.cli_available:
        return {
            "status": "ok",
            "detail": f"found {status.binaries.cli}" + (f" (version {status.version})" if status.version else ""),
        }
    return {
        "status": "missing",
        "detail": f"{CLI_BINARY} not found; install PhotoCraft or set PHOTOCRAFT_CLI",
        "remediation": "Install PhotoCraft, or point PHOTOCRAFT_CLI at the photocraft-cli binary.",
    }


def _check_app(status: Any) -> dict[str, Any]:
    if status.app_available:
        return {"status": "ok", "detail": f"found {status.binaries.app}"}
    return {
        "status": "missing",
        "detail": f"{APP_BINARY} not found; UI-driven actions are unavailable",
        # Deliberately not an error: the CLI alone covers headless work.
        "remediation": "Optional. Install the PhotoCraft desktop app to enable UI-driven actions.",
    }


def _check_control_port() -> dict[str, Any]:
    raw_port = os.environ.get("PHOTOCRAFT_CONTROL_PORT")
    if not raw_port or not raw_port.strip():
        return {
            "status": "not_configured",
            "detail": "PHOTOCRAFT_CONTROL_PORT is unset; UI-driven actions are unavailable",
            "remediation": (
                "Start PhotoCraft with `photocraft --control <port> "
                "--control-token-file <path>` and set this to that port."
            ),
        }
    try:
        endpoint = endpoint_from_env()
    except ControlChannelUnavailable as exc:
        return {
            "status": "error",
            "detail": str(exc),
            "remediation": (
                "Upstream generates a token when none is given and writes it to "
                "stderr; pass that token or its file through PHOTOCRAFT_CONTROL_TOKEN "
                "or PHOTOCRAFT_CONTROL_TOKEN_FILE."
            ),
        }
    if endpoint is None:
        return {
            "status": "not_configured",
            "detail": "PHOTOCRAFT_CONTROL_PORT is unset; UI-driven actions are unavailable",
            "remediation": "Start PhotoCraft with --control <port> and set this to that port.",
        }
    # The token is a secret: report that one was present, never its value.
    return {"status": "ok", "detail": f"control port {endpoint.port} is configured with a token"}


def doctor_report() -> dict[str, Any]:
    """Return the full self-check as a JSON-serialisable dict."""
    status = probe()
    checks = {
        "cli": _check_cli(status),
        "desktop_app": _check_app(status),
        "control_port": _check_control_port(),
        "environment": {name: os.environ.get(name) for name in ENV_VARS},
    }
    errors = [name for name, check in checks.items() if isinstance(check, dict) and check.get("status") == "error"]
    return {
        "adapter": "dcc-mcp-photocraft",
        "dcc": "photocraft",
        "status": "error" if errors else ("ok" if status.cli_available else "missing_host"),
        "headless_ready": status.cli_available,
        "ui_ready": status.app_available and checks["control_port"]["status"] == "ok",
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the doctor from the command line.

    Falls back to ``sys.argv`` so ``python -m dcc_mcp_photocraft.doctor --json``
    honours its flags: the module entry point is the only real one, and
    defaulting to an empty list would make ``--json`` a dead switch.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    report = doctor_report()
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"{report['adapter']}: {report['status']}")
        for name, check in report["checks"].items():
            if name == "environment":
                continue
            print(f"  {name}: {check['status']} - {check['detail']}")
    # A missing host is a reportable state, not a failure: only a malformed
    # configuration is an error worth a non-zero exit.
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
