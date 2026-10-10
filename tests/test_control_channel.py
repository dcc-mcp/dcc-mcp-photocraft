"""Tests for the loopback control-channel client.

These cover the contract that makes the channel safe to use: the auth handshake
happens before anything else, the port comes from configuration while PdfCraft
reads one from a file, and malformed input produces a typed error instead of a
silent success.
"""

from __future__ import annotations

import json

import pytest

from dcc_mcp_photocraft.control_channel import (
    ControlChannel,
    ControlChannelError,
    ControlChannelUnavailable,
    endpoint_from_env,
    read_endpoint,
    read_token,
)

from .conftest import TEST_TOKEN


def test_read_endpoint_accepts_a_configured_port():
    endpoint = read_endpoint(7878, token=TEST_TOKEN)

    assert endpoint.port == 7878
    assert endpoint.token == TEST_TOKEN


def test_read_endpoint_parses_a_numeric_string_port():
    """Ports arrive from environment variables, so strings must be accepted."""
    endpoint = read_endpoint("  8123  ", token=TEST_TOKEN)

    assert endpoint.port == 8123


def test_read_endpoint_rejects_a_non_numeric_port():
    with pytest.raises(ControlChannelUnavailable, match="is not a number"):
        read_endpoint("not-a-port", token=TEST_TOKEN)


def test_read_endpoint_rejects_an_empty_port():
    with pytest.raises(ControlChannelUnavailable, match="empty"):
        read_endpoint("", token=TEST_TOKEN)


def test_read_endpoint_rejects_an_out_of_range_port():
    # Refused rather than truncated: a truncated port would silently reach some
    # other service.
    with pytest.raises(ControlChannelUnavailable, match="out of range"):
        read_endpoint(70000, token=TEST_TOKEN)


def test_read_endpoint_rejects_port_zero():
    """Port 0 asks the OS to choose, which is meaningless for a client."""
    with pytest.raises(ControlChannelUnavailable, match="out of range"):
        read_endpoint(0, token=TEST_TOKEN)


def test_read_endpoint_rejects_a_boolean_port():
    with pytest.raises(ControlChannelUnavailable, match="must be a number"):
        read_endpoint(True, token=TEST_TOKEN)


def test_read_endpoint_requires_a_token():
    """The auth frame is mandatory upstream, so a port alone is never enough."""
    with pytest.raises(ControlChannelUnavailable, match="no control token"):
        read_endpoint(7878)


def test_read_token_prefers_a_literal_token():
    assert read_token("literal", None) == "literal"


def test_read_token_reads_a_token_file(tmp_path):
    path = tmp_path / "photocraft-control.token"
    path.write_text(TEST_TOKEN, encoding="utf-8")

    assert read_token(None, path) == TEST_TOKEN


def test_read_token_rejects_a_missing_file(tmp_path):
    with pytest.raises(ControlChannelUnavailable, match="cannot read"):
        read_token(None, tmp_path / "absent.token")


def test_read_token_rejects_an_empty_file(tmp_path):
    path = tmp_path / "empty.token"
    path.write_text("  ", encoding="utf-8")

    with pytest.raises(ControlChannelUnavailable, match="empty"):
        read_token(None, path)


def test_endpoint_from_env_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PHOTOCRAFT_CONTROL_PORT", raising=False)
    monkeypatch.delenv("PHOTOCRAFT_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("PHOTOCRAFT_CONTROL_TOKEN_FILE", raising=False)

    # An unconfigured channel is a normal state, not an error: it is needed only
    # for UI actions, and headless work does not use it.
    assert endpoint_from_env() is None


def test_endpoint_from_env_builds_an_endpoint(monkeypatch):
    monkeypatch.setenv("PHOTOCRAFT_CONTROL_PORT", "7878")
    monkeypatch.setenv("PHOTOCRAFT_CONTROL_TOKEN", TEST_TOKEN)

    endpoint = endpoint_from_env()

    assert endpoint is not None
    assert endpoint.port == 7878
    assert endpoint.token == TEST_TOKEN


def test_endpoint_from_env_accepts_an_explicit_mapping():
    endpoint = endpoint_from_env({"PHOTOCRAFT_CONTROL_PORT": "9100", "PHOTOCRAFT_CONTROL_TOKEN": TEST_TOKEN})

    assert endpoint is not None
    assert endpoint.port == 9100


def test_call_authenticates_then_returns_result(control_env, fake_server):
    endpoint = endpoint_from_env()

    result = ControlChannel(endpoint).call("ui.screenshot", {"path": "out.png"})

    assert result == {"echo": "ui.screenshot", "params": {"path": "out.png"}}
    # The auth frame is consumed by the channel, so only the real request is
    # visible to the app.
    assert [entry["method"] for entry in fake_server.received] == ["ui.screenshot"]


def test_call_fails_when_the_token_is_wrong(control_env, fake_server, monkeypatch):
    monkeypatch.setenv("PHOTOCRAFT_CONTROL_TOKEN", "f" * 64)
    endpoint = endpoint_from_env()

    with pytest.raises(ControlChannelError) as excinfo:
        ControlChannel(endpoint).call("ui.state")

    assert "authentication required" in str(excinfo.value)
    # The token must never appear in an error message.
    assert TEST_TOKEN not in str(excinfo.value)


def test_call_reports_an_unreachable_port(monkeypatch):
    monkeypatch.setenv("PHOTOCRAFT_CONTROL_PORT", "1")
    monkeypatch.setenv("PHOTOCRAFT_CONTROL_TOKEN", TEST_TOKEN)
    endpoint = endpoint_from_env()

    with pytest.raises(ControlChannelUnavailable, match="cannot reach"):
        ControlChannel(endpoint).call("ui.state")


def test_call_raises_on_an_error_reply(control_env, fake_server, monkeypatch):
    """PhotoCraft reports failures with ok=false, not a JSON-RPC error object."""

    def failing(connection):
        stream = connection.makefile("rb")
        try:
            for line in stream:
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                if message.get("method") == "auth":
                    connection.sendall(json.dumps({"id": message.get("id"), "ok": True, "result": {}}).encode() + b"\n")
                else:
                    connection.sendall(
                        json.dumps({"id": message.get("id"), "ok": False, "error": "unknown tool `foo`"}).encode()
                        + b"\n"
                    )
        except OSError:
            pass

    monkeypatch.setattr(fake_server, "_handle", failing)
    endpoint = endpoint_from_env()

    with pytest.raises(ControlChannelError, match="unknown tool"):
        ControlChannel(endpoint).call("ui.state")


def test_call_rejects_a_non_object_reply(control_env, fake_server, monkeypatch):
    def non_object(connection):
        stream = connection.makefile("rb")
        try:
            for line in stream:
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                payload = (
                    {"id": message.get("id"), "ok": True, "result": {}}
                    if message.get("method") == "auth"
                    else json.dumps([1, 2, 3])
                )
                connection.sendall((payload if isinstance(payload, str) else json.dumps(payload)).encode() + b"\n")
        except OSError:
            pass

    monkeypatch.setattr(fake_server, "_handle", non_object)
    endpoint = endpoint_from_env()

    with pytest.raises(ControlChannelError, match="non-object reply"):
        ControlChannel(endpoint).call("ui.state")


def test_call_detects_an_id_mismatch(control_env, fake_server, monkeypatch):
    def mismatched(connection):
        stream = connection.makefile("rb")
        try:
            for line in stream:
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                reply_id = 999 if message.get("method") != "auth" else message.get("id")
                connection.sendall(json.dumps({"id": reply_id, "ok": True, "result": {}}).encode() + b"\n")
        except OSError:
            pass

    monkeypatch.setattr(fake_server, "_handle", mismatched)
    endpoint = endpoint_from_env()

    with pytest.raises(ControlChannelError, match="does not match"):
        ControlChannel(endpoint).call("ui.state")


def test_call_raises_when_the_connection_closes_without_an_answer(control_env, fake_server, monkeypatch):
    def silent(connection):
        stream = connection.makefile("rb")
        try:
            for line in stream:
                if not line.strip():
                    continue
                message = json.loads(line.decode("utf-8"))
                if message.get("method") == "auth":
                    connection.sendall(json.dumps({"id": message.get("id"), "ok": True, "result": {}}).encode() + b"\n")
                # Close so the client sees EOF instead of blocking until its
                # socket timeout: this is the "answered auth then vanished" case.
                connection.close()
                return
        except OSError:
            pass

    monkeypatch.setattr(fake_server, "_handle", silent)
    endpoint = endpoint_from_env()

    # An abrupt close reaches the client as EOF on POSIX and as a connection
    # reset on Windows, so both spellings are accepted; what matters is that the
    # failure is typed as unavailable rather than returned as a result.
    with pytest.raises(ControlChannelUnavailable):
        ControlChannel(endpoint).call("ui.state")


def test_endpoint_is_rebuilt_per_call(control_env, fake_server, monkeypatch):
    """A change to the configured port must take effect without a restart."""
    first = endpoint_from_env()
    assert ControlChannel(first).call("ui.state")["echo"] == "ui.state"

    second = FakeControlServerHelper()
    second.start()
    try:
        monkeypatch.setenv("PHOTOCRAFT_CONTROL_PORT", str(second.port))
        monkeypatch.setenv("PHOTOCRAFT_CONTROL_TOKEN", TEST_TOKEN)
        rebuilt = endpoint_from_env()
        assert rebuilt.port == second.port
        assert ControlChannel(rebuilt).call("ui.state")["echo"] == "ui.state"
    finally:
        second.stop()


class FakeControlServerHelper:
    """A second listener, to prove the endpoint is not cached."""

    def __init__(self) -> None:
        from .conftest import FakeControlServer

        self._inner = FakeControlServer()

    @property
    def port(self) -> int:
        return self._inner.port

    def start(self) -> None:
        self._inner.start()

    def stop(self) -> None:
        self._inner.stop()
