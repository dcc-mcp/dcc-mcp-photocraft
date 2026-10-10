"""Install SOP report assembly for the PhotoCraft adapter.

An Install SOP report is the contract an adapter publishes about its own
install state: what it checked, what passed, and what the caller should do
next. Core owns the schema and the validator, and this module owns the part
only the adapter knows -- which checks apply to PhotoCraft and what their
results mean.

The report is validated before it is returned, and it is validated in CI
against a report the doctor actually produces. That second half is the point:
the PIP-3990 incident shipped a schema in every adapter and ran a report
through it in none, which is how a wrong value reached a release.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dcc_mcp_core import (
    install_sop_report_schema_version,
    validate_install_sop_report,
)

from .__version__ import __version__
from .doctor import doctor_report

# Step status values. The schema constrains these to a non-empty string rather
# than an enum, so the vocabulary is the adapter's to define; these three are
# what the doctor checks can produce.
STEP_STATUS_PASS = "pass"
STEP_STATUS_FAIL = "fail"
STEP_STATUS_SKIP = "skip"

# Overall report status values, from the schema enum.
REPORT_STATUS_OK = "ok"
REPORT_STATUS_FAILED = "failed"
REPORT_STATUS_PARTIAL = "partial"

RECEIPT_PATH = "dcc-mcp-photocraft-install.json"


def _core_version() -> str:
    """Return the installed Core version, falling back when it is unreadable.

    Core is a hard dependency, so a missing distribution means a broken
    environment rather than a normal state; reporting it as unknown keeps the
    report well-formed instead of raising at the worst moment.
    """
    try:
        from importlib.metadata import version

        return version("dcc-mcp-core")
    except Exception:
        return "unknown"


def _step(step_id: str, status: str, description: str, message: str = "") -> dict[str, Any]:
    step: dict[str, Any] = {"id": step_id, "status": status, "description": description}
    if message:
        step["message"] = message
    return step


def _next_step(step_id: str, command: list[str], why: str, description: str) -> dict[str, Any]:
    # Ids must be unique across the report: the validator rejects duplicates so
    # that a caller can address a next step unambiguously.
    return {
        "id": step_id,
        "description": description,
        "why": why,
        "command": command,
    }


def _report_status(steps: list[dict[str, Any]]) -> str:
    """Derive the overall status from the step outcomes.

    Derived rather than assigned so the two can never disagree: a hand-written
    status next to a list of steps is exactly the drift the schema is meant to
    catch.
    """
    statuses = {step["status"] for step in steps}
    if STEP_STATUS_FAIL in statuses:
        return REPORT_STATUS_FAILED
    if STEP_STATUS_SKIP in statuses:
        # Optional checks were skipped, so the install works but is not
        # complete.
        return REPORT_STATUS_PARTIAL
    return REPORT_STATUS_OK


def _verify(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe how to confirm the install, and what went wrong if it failed."""
    failed = next((step for step in steps if step["status"] == STEP_STATUS_FAIL), None)
    return {
        "directly_usable": failed is None,
        "failure_stage": failed["id"] if failed else None,
        "failure_reason": failed.get("message") if failed else None,
    }


def build_report(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the Install SOP report from a real doctor run.

    Defaults to running the doctor, so the report describes actual machine
    state rather than a stored expectation.
    """
    doctor = report if report is not None else doctor_report()
    checks = doctor.get("checks", {})

    steps: list[dict[str, Any]] = []
    next_steps: list[dict[str, Any]] = []

    headless_ready = bool(doctor.get("headless_ready"))

    cli = checks.get("cli", {})
    steps.append(
        _step(
            "locate-photocraft-cli",
            STEP_STATUS_PASS if headless_ready else STEP_STATUS_FAIL,
            "Locate the photocraft-cli binary",
            # A failing step must always carry a reason: ``verify.failure_reason``
            # is read from it, and an empty reason would tell a caller that the
            # install is broken without saying why.
            cli.get("detail") or "photocraft-cli was not found on PATH or in the install directories",
        )
    )

    app = checks.get("desktop_app", {})
    app_ready = app.get("status") == "ok"
    steps.append(
        _step(
            "locate-photocraft-app",
            STEP_STATUS_PASS if app_ready else STEP_STATUS_SKIP,
            "Locate the PhotoCraft desktop app (optional; enables UI actions)",
            app.get("detail", ""),
        )
    )

    control = checks.get("control_port", {})
    control_status = control.get("status")
    if control_status == "error":
        step_status = STEP_STATUS_FAIL
    elif control_status == "ok":
        step_status = STEP_STATUS_PASS
    else:
        step_status = STEP_STATUS_SKIP
    steps.append(
        _step(
            "resolve-control-port",
            step_status,
            "Resolve the control port published by the running app (optional)",
            control.get("detail", ""),
        )
    )

    if not headless_ready:
        next_steps.append(
            _next_step(
                "install-photocraft",
                ["echo", "Install PhotoCraft from https://github.com/storytold/photocraft/releases"],
                "The adapter is installed but PhotoCraft is not reachable, so headless actions cannot run. "
                "This step is emitted only after the adapter install, so it must point at the upstream "
                "PhotoCraft release, not at the adapter again.",
                "Install PhotoCraft so the adapter can drive it",
            )
        )
        next_steps.append(
            _next_step(
                "verify-photocraft-cli",
                ["photocraft-cli", "--version"],
                "Confirm the PhotoCraft CLI is on PATH, or set PHOTOCRAFT_CLI to its full path.",
                "Verify the PhotoCraft CLI is reachable",
            )
        )

    sop_report: dict[str, Any] = {
        "schema_version": install_sop_report_schema_version(),
        "status": _report_status(steps),
        "dcc_type": "photocraft",
        "adapter_version": __version__,
        "core_version": _core_version(),
        "steps": steps,
        "next_steps": next_steps,
        "receipt_path": RECEIPT_PATH,
        "verify": _verify(steps),
    }
    return sop_report


def validated_report(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the report and validate it before returning.

    Validating here rather than only in tests means an invalid report can never
    be published by accident: the failure surfaces at the call site.
    """
    sop = build_report(report)
    validate_install_sop_report(sop)
    return sop


def write_receipt(path: str | Path, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the report and write it as the install receipt."""
    sop = validated_report(report)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(sop, indent=2, sort_keys=True), encoding="utf-8")
    return sop
