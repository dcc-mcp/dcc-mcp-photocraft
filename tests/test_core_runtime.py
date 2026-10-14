"""Real released-Core lifecycle and Skill-dispatch regressions; host calls are fake."""

from __future__ import annotations

import json
import socket
import urllib.request

import pytest

from dcc_mcp_photocraft import server as server_module
from dcc_mcp_photocraft.cli_transport import CliTransport


def instance(tmp_path, **kwargs):
    return server_module.PhotoCraftMcpServer(
        port=0,
        gateway_port=0,
        registry_dir=str(tmp_path / "registry"),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_checkpoint_tools=False,
        enable_telemetry=False,
        **kwargs,
    )


def test_real_readiness_binding_and_failed_start_cleanup(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("listener failed")

    monkeypatch.setattr(server_module.DccServerBase, "start", fail)
    server = instance(tmp_path)
    with pytest.raises(RuntimeError, match="listener failed"):
        server.start()
    assert server._readiness.server is server
    assert not server._host_driver.is_running
    with pytest.raises(RuntimeError, match="create a new"):
        server.start()


def test_failed_stop_still_clears_singleton(monkeypatch):
    class Failed:
        def stop(self):
            raise RuntimeError("stop failed")

    monkeypatch.setattr(server_module, "_server", Failed())
    with pytest.raises(RuntimeError, match="stop failed"):
        server_module.stop_server()
    assert server_module._server is None


def test_real_core_mcp_fixed_wrappers_and_instance_configuration(monkeypatch, tmp_path):
    binary = tmp_path / "configured-cli"
    binary.touch()
    observed = []

    def record(transport, value):
        observed.append((transport.binary, transport.read_root, transport.write_root))
        return value

    monkeypatch.setattr(CliTransport, "commands", lambda self: record(self, [{"id": "host.dynamic.command"}]))
    monkeypatch.setattr(CliTransport, "methods", lambda self: record(self, ["doc.inspect"]))
    monkeypatch.setattr(CliTransport, "info", lambda self, path: record(self, {"path": path, "layers": 2}))
    monkeypatch.setenv("PHOTOCRAFT_CLI", "wrong-environment-binary")
    monkeypatch.setenv("PHOTOCRAFT_READ_ROOT", "wrong-environment-read-root")
    monkeypatch.setenv("PHOTOCRAFT_WRITE_ROOT", "wrong-environment-write-root")
    server = instance(tmp_path, cli_binary=str(binary), read_root=str(tmp_path), write_root=str(tmp_path))
    ui_calls = []

    class Channel:
        def call(self, method, params):
            ui_calls.append((method, params))
            return {"fake_host": True}

    monkeypatch.setattr(server, "control_channel", lambda: Channel())
    session = None
    request_id = 0

    def rpc(method, params):
        nonlocal session, request_id
        request_id += 1
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        req = urllib.request.Request(
            handle.mcp_url(),
            headers=headers,
            data=json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode(),
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            session = response.headers.get("Mcp-Session-Id", session)
            return json.load(response)["result"]

    def call(name, args=None, error=False):
        result = rpc("tools/call", {"name": name, "arguments": args or {}})
        assert result.get("isError", False) is error, result
        if error:
            return result
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    try:
        handle = server.start(install_atexit_hook=False)
        assert server.start(install_atexit_hook=False) is handle
        assert handle.mcp_url().startswith("http://127.0.0.1:")
        result = rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "photocraft-core-regression", "version": "1"},
            },
        )
        assert result["serverInfo"]["name"] == "dcc-mcp-photocraft"
        assert any(x["name"] == "photocraft-core" for x in call("list_skills")["skills"])
        loaded = call("load_skill", {"skill_name": "photocraft-core"})
        assert loaded["loaded"] is True
        required = {
            "photocraft_core__document_info",
            "photocraft_core__list_photocraft_commands",
            "photocraft_core__list_photocraft_methods",
            "photocraft_core__ui_screenshot",
            "photocraft_core__ui_inspect",
        }
        assert required <= set(loaded["registered_tools"])
        assert call("photocraft_core__document_info", {"path": "original.pcraft"})["result"]["layers"] == 2
        assert call("photocraft_core__list_photocraft_commands")["commands"] == [{"id": "host.dynamic.command"}]
        assert call("photocraft_core__list_photocraft_methods")["methods"] == ["doc.inspect"]
        assert call("photocraft_core__ui_screenshot")["result"] == {"fake_host": True}
        assert call("photocraft_core__ui_inspect")["result"] == {"fake_host": True}
        call("photocraft_core__document_info", {"path": True}, error=True)
        assert ui_calls == [("ui.screenshot", {}), ("ui.inspect", {})]
        assert observed and all(row == (str(binary), str(tmp_path), str(tmp_path)) for row in observed)
    finally:
        server.stop()
    assert not server._host_driver.is_running
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", handle.port)) != 0
