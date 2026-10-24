#!/usr/bin/env python3
"""Opt-in read-only Core MCP acceptance for an official PhotoCraft CLI.

Requires the installed adapter/Core and jsonschema, without a desktop or the
optional MCP SDK. Reads only an original synthetic PNG. It never invokes image
editing, export, UI control, a gateway or an installer. CLI process completion
is provided by the production subprocess.run transport; this script does not
claim an independent native PID sweep or desktop/editing qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import socket
import struct
import sys
import traceback
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
READ_TOOLS = ("list_photocraft_commands", "list_photocraft_methods", "document_info")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_hashes() -> dict[str, str]:
    files = [Path(__file__).resolve(), ROOT / "pyproject.toml"]
    files.extend(path for path in (ROOT / "src").rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)}


def png_bytes(width: int = 16, height: int = 12) -> bytes:
    """Create a deterministic original RGB PNG using the standard library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    pixels = b"".join(b"\0" + b"".join(bytes((x * 11, y * 17, 83)) for x in range(width)) for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


class CoreClient:
    """Small synchronous MCP client restricted to the owned loopback endpoint."""

    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
            raise ValueError("acceptance requires an explicit HTTP loopback endpoint")
        self.url, self.session, self.request_id = url, None, 0
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.calls: list[dict[str, Any]] = []

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request = {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        with self.http.open(
            urllib.request.Request(self.url, data=json.dumps(request).encode(), headers=headers), timeout=120
        ) as response:
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            reply = json.load(response)
        self.calls.append({"request": request, "response": reply})
        if reply.get("id") != request["id"] or "error" in reply or not isinstance(reply.get("result"), dict):
            raise RuntimeError("Core MCP returned an error, mismatched id or invalid result")
        return reply["result"]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if response.get("isError"):
            raise RuntimeError(f"Core tool {name} returned an error: {response}")
        value = response.get("structuredContent")
        if value is None:
            content = response.get("content", [])
            if len(content) != 1 or content[0].get("type") != "text":
                raise RuntimeError("Core tool returned unexpected content")
            value = json.loads(content[0]["text"])
        if isinstance(value, dict) and (value.get("ok") is False or value.get("success") is False):
            raise RuntimeError(f"Core tool {name} reported failure: {value}")
        return value


def run(cli: Path, output: Path, expected_hash: str) -> dict[str, Any]:
    from jsonschema import Draft202012Validator

    from dcc_mcp_photocraft.cli_transport import CliTransport
    from dcc_mcp_photocraft.server import PhotoCraftMcpServer

    cli = cli.resolve(strict=True)
    if sha(cli) != expected_hash:
        raise ValueError("official CLI SHA-256 mismatch; no executable was started")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "original.png"
    source.write_bytes(png_bytes())
    before = sha(source)
    registry = output / "registry"
    report: dict[str, Any] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "read-only CLI/Core MCP; synthetic PNG; no editing, GUI, gateway or installation",
        "versions": {name: importlib.metadata.version(name) for name in ("dcc-mcp-core", "jsonschema")},
        "python": sys.version,
        "cli_sha256": expected_hash,
        "input_sha256": before,
        "source_hashes_before": source_hashes(),
        "checks": {},
    }
    server, handle, client = None, None, None

    def check(name: str, passed: Any) -> None:
        report["checks"][name] = bool(passed)
        if not passed:
            raise AssertionError(name)

    try:
        report["official_version"] = CliTransport(cli).run(["--version"]).strip()
        server = PhotoCraftMcpServer(
            port=0,
            gateway_port=0,
            gateway_remote_port=0,
            registry_dir=str(registry),
            enable_gateway_failover=False,
            enable_file_logging=False,
            enable_job_persistence=False,
            enable_checkpoint_persistence=False,
            enable_checkpoint_tools=False,
            enable_telemetry=False,
            cli_binary=str(cli),
            read_root=str(output),
            write_root=None,
        )
        handle = server.start(install_atexit_hook=False)
        client = CoreClient(handle.mcp_url())
        initialized = client.rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "photocraft-native-readback", "version": "1"},
            },
        )
        check("core_identity", initialized["serverInfo"]["name"] == "dcc-mcp-photocraft")
        check("skill_discovered", any(s["name"] == "photocraft-core" for s in client.call("list_skills")["skills"]))
        check("skill_loaded", client.call("load_skill", {"skill_name": "photocraft-core"})["loaded"])
        tools, cursor = {}, None
        while True:
            page = client.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.update((tool["name"], tool) for tool in page["tools"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        results = {}
        for name in READ_TOOLS:
            public = name if name in tools else "photocraft_core__" + name
            arguments = {"path": source.name} if name == "document_info" else {}
            Draft202012Validator(tools[public]["inputSchema"]).validate(arguments)
            results[name] = client.call(public, arguments)
        check("native_commands_reported", bool(results["list_photocraft_commands"]["commands"]))
        check("native_methods_reported", "doc.inspect" in results["list_photocraft_methods"]["methods"])
        info = results["document_info"]["result"]
        check("synthetic_dimensions_readback", info["width"] == 16 and info["height"] == 12)
        check("no_write_authority", server.write_root is None)
        report["results"] = results
        report["tool_schemas"] = {
            name: tool["inputSchema"] for name, tool in tools.items() if any(name.endswith(read) for read in READ_TOOLS)
        }
    except BaseException:
        report["failure"] = traceback.format_exc()
        raise
    finally:
        if server is not None:
            try:
                server.stop()
                report["checks"]["core_stop_succeeded"] = True
            except Exception:
                report["checks"]["core_stop_succeeded"] = False
                report["cleanup_failure"] = traceback.format_exc()
            report["checks"]["dispatcher_stopped"] = not server._host_driver.is_running
        if handle is not None:
            with socket.socket() as probe:
                probe.settimeout(1)
                report["checks"]["core_listener_closed"] = probe.connect_ex(("127.0.0.1", handle.port)) != 0
        services = registry / "services.json"
        report["checks"]["registry_empty"] = not json.loads(services.read_text()) if services.exists() else True
        report["checks"]["synthetic_input_unchanged"] = sha(source) == before
        report["checks"]["official_cli_unchanged"] = sha(cli) == expected_hash
        report["source_hashes_after"] = source_hashes()
        report["checks"]["adapter_sources_unchanged"] = report["source_hashes_before"] == report["source_hashes_after"]
        report["calls"] = client.calls if client else []
        report["all_passed"] = "failure" not in report and all(report["checks"].values())
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        dump(output / "report.json", report)
        dump(output / "SHA256SUMS.json", {p.name: sha(p) for p in (source, output / "report.json")})
    if not report["all_passed"]:
        raise AssertionError("readback acceptance cleanup or integrity check failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-cli-sha256", required=True)
    args = parser.parse_args()
    # This command owns its process environment. Do not inherit an operator's
    # gateway, desktop attachment, custom skills or filesystem authorities.
    for name in tuple(os.environ):
        if name.startswith(("DCC_MCP_", "PHOTOCRAFT_")):
            os.environ.pop(name)
    os.environ["DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS"] = "1"
    report = run(args.cli, args.output, args.expected_cli_sha256)
    print(
        json.dumps(
            {
                "all_passed": report["all_passed"],
                "checks": len(report["checks"]),
                "report": str(args.output / "report.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
