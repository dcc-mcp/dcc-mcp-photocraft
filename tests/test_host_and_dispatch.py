"""Tests for host detection and action dispatch.

A recurring failure mode for an adapter that drives an external binary is
reporting success when the host is missing. These tests pin the opposite
contract: a missing host is reported, never faked.
"""

from __future__ import annotations

import pytest

from dcc_mcp_photocraft import actions, capability_dispatch, host
from dcc_mcp_photocraft.capability_dispatch import ActionError, dispatch


@pytest.fixture
def clean_env(monkeypatch):
    """Clear the adapter environment so tests do not inherit a real host."""
    for name in (
        "PHOTOCRAFT_CLI",
        "PHOTOCRAFT_APP",
        "PHOTOCRAFT_CONTROL_PORT",
        "PHOTOCRAFT_CONTROL_TOKEN",
        "PHOTOCRAFT_CONTROL_TOKEN_FILE",
        "PHOTOCRAFT_READ_ROOT",
        "PHOTOCRAFT_WRITE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_discover_reports_absent_binaries(monkeypatch, tmp_path):
    """With nothing on PATH, both binaries must be reported as absent."""
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    status = host.probe()

    assert status.cli_available is False
    assert status.app_available is False
    assert "not found" in status.describe()


def test_discover_finds_cli_in_search_dir(monkeypatch, tmp_path):
    # The candidate name is platform-specific (.exe on Windows), so take it from
    # the module under test rather than assuming a bare name.
    fake = tmp_path / ("photocraft-cli" + host._SUFFIXES[0])
    fake.write_text("", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | 0o111)
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    assert host.discover().cli is not None
    assert host.probe().cli_available is True


def test_discover_does_not_execute_the_binary(monkeypatch, tmp_path):
    """Detection must be a filesystem lookup, not a subprocess spawn."""
    calls = []
    monkeypatch.setattr(host, "cli_version", lambda *a, **k: calls.append(True))
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    host.discover()

    assert calls == []


def test_actions_report_missing_host_without_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(host, "_which", lambda name: None)
    monkeypatch.setattr(host, "_search_dirs", lambda: [tmp_path])

    report = actions.host_status()

    # A missing host is reportable state, not an exception.
    assert report["ok"] is True
    assert report["result"]["available"] is False


def _missing_cli(*args, **kwargs):
    from dcc_mcp_photocraft.cli_transport import CliNotFound

    raise CliNotFound("photocraft-cli was not found")


def test_list_commands_reports_error_when_host_missing(monkeypatch, clean_env):
    monkeypatch.setattr(capability_dispatch, "_transport", _missing_cli)

    report = actions.list_photocraft_commands()

    assert report["ok"] is False
    assert "error" in report


def test_dispatch_routes_cli_actions(monkeypatch, clean_env):
    seen = {}

    class FakeTransport:
        def __init__(self, *args, **kwargs):
            seen["kwargs"] = kwargs

        def commands(self):
            return [{"id": "file.new"}]

        def methods(self):
            return ["engine.execute", "doc.open", "methods"]

        def info(self, path):
            seen["path"] = path
            return {"layers": 2}

        def run_command(self, name, params):
            seen["method"] = name
            seen["params"] = params
            return {"ok": True}

    monkeypatch.setattr(capability_dispatch, "_transport", lambda: FakeTransport())

    assert dispatch("list_commands")["commands"] == [{"id": "file.new"}]
    assert dispatch("list_methods")["methods"] == ["engine.execute", "doc.open", "methods"]
    assert dispatch("document_info", {"path": "a.pcraft"})["result"] == {"layers": 2}
    assert seen["path"] == "a.pcraft"

    # An unrecognised action name is forwarded as a PhotoCraft command id: the
    # table is owned by upstream and discovered at runtime, so the adapter must
    # not reject names it does not know.
    assert dispatch("file.new", {"width": 800})["result"] == {"ok": True}
    assert seen["method"] == "file.new"


def test_dispatch_requires_path_for_document_info(monkeypatch):
    monkeypatch.setattr(capability_dispatch, "_transport", lambda: object())

    with pytest.raises(ActionError, match="requires a `path`"):
        dispatch("document_info", {})


def test_ui_action_without_control_port_explains_how_to_fix(clean_env):
    with pytest.raises(ActionError) as excinfo:
        dispatch("ui_screenshot")

    message = str(excinfo.value)
    assert "--control" in message
    assert "PHOTOCRAFT_CONTROL_PORT" in message


def test_ui_action_uses_control_channel(monkeypatch, control_env, fake_server):
    """A UI action must reach the control channel and authenticate."""

    class FakeServer:
        def control_channel(self):
            from dcc_mcp_photocraft.control_channel import ControlChannel, endpoint_from_env

            return ControlChannel(endpoint_from_env())

    monkeypatch.setattr(capability_dispatch, "_server", lambda: FakeServer())

    result = dispatch("ui_screenshot", {"path": "out.png"})

    assert result["method"] == "ui.screenshot"
    assert fake_server.received[-1]["params"] == {"path": "out.png"}


def test_action_error_is_normalized_in_envelope(monkeypatch):
    monkeypatch.setattr(capability_dispatch, "_transport", _missing_cli)

    report = actions.document_info("a.pcraft")

    assert report["ok"] is False
    assert report["error_type"] == "ActionError"


def test_control_channel_errors_reach_callers_as_action_error(monkeypatch, control_env, fake_server):
    """Every control-channel failure must surface as ``ActionError``.

    A reply carrying ``ok: false`` raises the base ``ControlChannelError``, not
    the unavailable subclass. Catching only the subclass would let that case
    escape as a raw error, so a caller catching ``ActionError`` would miss it.
    """
    from dcc_mcp_photocraft.control_channel import ControlChannelError

    class FailingChannel:
        def call(self, method, params):
            raise ControlChannelError("unknown tool `foo`")

    class FakeServer:
        def control_channel(self):
            return FailingChannel()

    monkeypatch.setattr(capability_dispatch, "_server", lambda: FakeServer())

    with pytest.raises(ActionError, match="unknown tool"):
        dispatch("ui_screenshot")


def test_discover_prefers_a_configured_cli_path(monkeypatch, tmp_path, clean_env):
    """The CLI the adapter will drive is the configured one, so report it."""
    from dcc_mcp_photocraft import host as host_module

    configured = tmp_path / "custom-photocraft-cli"
    configured.write_text("", encoding="utf-8")
    # Not on PATH and not in the search dirs: found only via PHOTOCRAFT_CLI.
    monkeypatch.setattr(host_module, "_which", lambda name: None)
    monkeypatch.setattr(host_module, "_search_dirs", lambda: [])
    monkeypatch.setenv("PHOTOCRAFT_CLI", str(configured))

    status = host_module.probe()

    assert status.cli_available is True
    assert status.binaries.cli == configured


def test_discover_ignores_a_stale_configured_cli_path(monkeypatch, tmp_path, clean_env):
    """A configured path that no longer exists must fall back, not fail."""
    from dcc_mcp_photocraft import host as host_module

    on_path = tmp_path / ("photocraft-cli" + host_module._SUFFIXES[0])
    on_path.write_text("", encoding="utf-8")
    on_path.chmod(on_path.stat().st_mode | 0o111)
    monkeypatch.setattr(host_module, "_which", lambda name: on_path)
    monkeypatch.setattr(host_module, "_search_dirs", lambda: [])
    monkeypatch.setenv("PHOTOCRAFT_CLI", str(tmp_path / "absent-cli"))

    assert host_module.probe().cli_available is True
