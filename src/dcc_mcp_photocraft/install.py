"""Install-time reporting for the PhotoCraft adapter.

This adapter cannot install PhotoCraft for the user: upstream ships it as a Rust
application, and the CLI binary arrives inside the GUI packages or as a
standalone archive. What an install step can do is verify that a host is
reachable and tell the user exactly how to fix it when one is not, rather than
leaving them with a server that starts and then fails on the first tool call.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .__version__ import __version__
from .doctor import doctor_report


def _print_report(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"dcc-mcp-photocraft {__version__}")
    print(f"  status: {report['status']}")
    print(f"  headless ready: {report['headless_ready']}")
    print(f"  ui ready: {report['ui_ready']}")
    for name, check in report["checks"].items():
        if name == "environment":
            continue
        print(f"  {name}: {check['status']} - {check['detail']}")
        remediation = check.get("remediation")
        if remediation:
            print(f"      fix: {remediation}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the install self-check."""
    parser = argparse.ArgumentParser(
        prog="dcc-mcp-photocraft-install",
        description="Verify that this machine can drive PhotoCraft.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    report = doctor_report()
    _print_report(report, args.as_json)

    if report["status"] == "error":
        return 1
    if not report["headless_ready"]:
        # A missing host is not an install failure, but it does mean the adapter
        # cannot do anything useful yet, so say so with a non-zero exit.
        print("PhotoCraft was not found; headless actions are unavailable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
