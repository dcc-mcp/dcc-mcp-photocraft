"""Explicit foreground service; no desktop startup hooks or installation edits."""

import argparse
import json
import signal
import threading
from pathlib import Path

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Experimental PhotoCraft headless adapter")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    from dcc_mcp_core import capture_bootstrap_errors

    with capture_bootstrap_errors(
        "photocraft", adapter_version=__version__, min_core_version="0.20.41"
    ):
        from .paths import Workspace
        from .server import PhotoCraftServer

        server = PhotoCraftServer(
            args.executable, Workspace(args.input_root, args.output_root), port=args.port
        )
        stopped = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        try:
            server.start()
            print(
                json.dumps({"mcp_url": server.mcp_url, "instance_id": server.instance_id}),
                flush=True,
            )
            stopped.wait()
        finally:
            server.stop()


if __name__ == "__main__":
    main()
