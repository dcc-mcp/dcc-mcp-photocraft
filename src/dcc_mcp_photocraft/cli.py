"""Command line entry point for the PhotoCraft adapter."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .__version__ import __version__
from .host import probe


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcc-mcp-photocraft",
        description="PhotoCraft adapter for the DCC Model Context Protocol.",
    )
    parser.add_argument("--version", action="version", version=f"dcc-mcp-photocraft {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run the MCP server for PhotoCraft.")
    subparsers.add_parser("status", help="Report the detected PhotoCraft host.")
    return parser


def _cmd_status() -> int:
    status = probe()
    print(json.dumps({"available": status.cli_available, "detail": status.describe()}, indent=2))
    return 0 if status.cli_available else 1


def _cmd_serve() -> int:
    from .server import main as server_main

    server_main()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Route the command line. ``serve`` stays the default for no arguments."""
    parser = _build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    # The historical contract for these adapters is that a bare invocation
    # starts the server, so an absent subcommand means ``serve``.
    if args.command is None or args.command == "serve":
        return _cmd_serve()
    if args.command == "status":
        return _cmd_status()
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
