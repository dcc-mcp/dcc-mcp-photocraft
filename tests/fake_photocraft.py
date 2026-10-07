"""Local subprocess fixture for the MCP contract; this is not PhotoCraft."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

TOOL_NAMES = (
    "doc_new",
    "doc_open",
    "doc_save",
    "doc_export",
    "doc_inspect",
    "doc_render_preview",
    "doc_select",
    "doc_close",
    "session_list",
    "command_list",
    "command_run",
)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument("--log", type=Path, required=True)
    options, remaining = parser.parse_known_args()

    def record(event: str, **values: object) -> None:
        with options.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid(), **values}) + "\n")

    def reply(request_id: object, result: object) -> None:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)

    if "--version" in remaining:
        record("version", argv=remaining)
        version = "0.2.1" if options.scenario == "wrong-version" else "0.2.0"
        print(f"photocraft-cli {version} (fake subprocess contract fixture)", flush=True)
        return

    record("started", argv=remaining)
    previous_call_id: object = None
    schema_path = (
        Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_photocraft" / "upstream_schemas.json"
    )
    schemas = json.loads(schema_path.read_text(encoding="utf-8"))
    if options.scenario == "missing-schema":
        del schemas["doc_new"]["properties"]["background"]

    for line in sys.stdin:
        message = json.loads(line)
        request_id = message.get("id")
        method = message.get("method")
        record("request", message=message)
        if method == "initialize":
            capabilities = {} if options.scenario == "missing-capability" else {"tools": {}}
            reply(
                request_id,
                {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": capabilities,
                    "serverInfo": {"name": "photocraft", "version": "0.2.0"},
                },
            )
        elif method == "tools/list":
            names = [
                name
                for name in TOOL_NAMES
                if not (options.scenario == "missing-tool" and name == "doc_save")
            ]
            reply(
                request_id,
                {
                    "tools": [
                        {
                            "name": name,
                            "description": "Test fixture only",
                            "inputSchema": {
                                "type": "object",
                                **schemas[name],
                            },
                        }
                        for name in names
                    ]
                },
            )
            if options.scenario == "idle-exit":
                exit_trigger = options.log.with_suffix(".exit")
                deadline = time.monotonic() + 10
                while not exit_trigger.exists() and time.monotonic() < deadline:
                    time.sleep(0.025)
                record("idle_exit")
                os._exit(19)
        elif method == "tools/call":
            params = message["params"]
            arguments = params.get("arguments", {})
            record("call", request_id=request_id, name=params["name"], arguments=arguments)
            payload = {
                "marker": arguments.get("marker", "unmarked"),
                "name": params["name"],
            }
            result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
            if arguments.get("marker") == "fault":
                if options.scenario == "process-exit":
                    os._exit(17)
                if options.scenario == "slow":
                    time.sleep(1.5)
                    record("late_reply_attempt", request_id=request_id)
                elif options.scenario in {
                    "wrong-id",
                    "stale-id",
                    "wrong-then-correct",
                    "stale-then-correct",
                }:
                    response_id = (
                        previous_call_id
                        if options.scenario.startswith("stale")
                        else "unrelated-request"
                    )
                    record("uncorrelated_reply", request_id=response_id)
                    if options.scenario.endswith("-then-correct"):
                        # Send both frames in one write so the matching reply is
                        # already queued when the client notices the wrong ID.
                        record("paired_replies", ids=[response_id, request_id])
                        print(
                            "\n".join(
                                json.dumps({"jsonrpc": "2.0", "id": value, "result": result})
                                for value in (response_id, request_id)
                            ),
                            flush=True,
                        )
                    else:
                        reply(response_id, result)
                    previous_call_id = request_id
                    continue
                elif options.scenario == "missing-id":
                    print(json.dumps({"jsonrpc": "2.0", "result": result}), flush=True)
                    record("uncorrelated_reply")
                    previous_call_id = request_id
                    continue
            reply(request_id, result)
            previous_call_id = request_id
        elif request_id is not None:
            reply(request_id, {})
    record("stdin_closed")


if __name__ == "__main__":
    main()
