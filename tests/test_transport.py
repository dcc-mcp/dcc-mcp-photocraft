"""Official SDK / real subprocess tests; these do not claim real PhotoCraft E2E."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from dcc_mcp_photocraft.errors import AdapterError
from dcc_mcp_photocraft.paths import Workspace
from dcc_mcp_photocraft.transport import ManagedPhotoCraft

FAKE_SERVER = Path(__file__).with_name("fake_photocraft.py")


def records(path: Path, event: str | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if event is None or row["event"] == event]


def process_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return (
                bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def assert_process_stopped(pid: int) -> None:
    deadline = time.monotonic() + 5
    while process_running(pid) and time.monotonic() < deadline:
        time.sleep(0.025)
    assert not process_running(pid), f"Managed fixture process {pid} was not reaped"


@pytest.fixture
def managed_factory(tmp_path):
    clients = []

    def create(scenario="healthy", *, timeout_seconds=2.0):
        case_root = tmp_path / str(len(clients))
        case_root.mkdir()
        input_root = case_root / "inputs"
        output_root = case_root / "outputs"
        input_root.mkdir()
        output_root.mkdir()
        log = case_root / "fake-process.jsonl"
        client = ManagedPhotoCraft(
            executable=Path(sys.executable),
            executable_args=[str(FAKE_SERVER), "--scenario", scenario, "--log", str(log)],
            workspace=Workspace(input_root, output_root),
            timeout_seconds=timeout_seconds,
        )
        clients.append((client, log))
        return client, log

    yield create

    for client, log in reversed(clients):
        client.close()
        for row in records(log, "started"):
            assert_process_stopped(row["pid"])


def test_one_managed_process_preserves_distinct_call_results(managed_factory):
    client, log = managed_factory()
    client.start()

    first = client.call("doc_inspect", {"marker": "first-result"})
    second = client.call("doc_inspect", {"marker": "second-result"})

    assert "first-result" in json.dumps(first)
    assert "second-result" not in json.dumps(first)
    assert "second-result" in json.dumps(second)
    assert "first-result" not in json.dumps(second)
    calls = records(log, "call")
    assert len(calls) == 2
    assert calls[0]["request_id"] != calls[1]["request_id"]
    assert len(records(log, "started")) == 1
    assert len(records(log, "version")) == 1


def test_close_reaps_owned_process_and_is_idempotent(managed_factory):
    client, log = managed_factory()
    client.start()
    pid = records(log, "started")[0]["pid"]
    assert process_running(pid)

    client.close()
    client.close()

    assert client.status()["state"] == "closed"
    assert_process_stopped(pid)
    with pytest.raises(AdapterError) as error:
        client.call("doc_inspect", {"marker": "after-close"})
    assert error.value.code == "connection_unavailable"
    assert records(log, "call") == []


def test_idle_process_exit_is_detected_without_another_tool_call(managed_factory):
    client, log = managed_factory("idle-exit")
    client.start()
    pid = records(log, "started")[0]["pid"]
    assert client.status()["state"] == "connected"
    assert process_running(pid)

    # This local fixture signal makes the child exit after startup is observed.
    # No MCP call is made to discover the disconnect or wake the owner queue.
    log.with_suffix(".exit").write_text("exit now", encoding="utf-8")
    deadline = time.monotonic() + 5
    while client.status()["state"] == "connected" and time.monotonic() < deadline:
        time.sleep(0.025)

    assert client.status()["state"] != "connected"
    assert len(records(log, "idle_exit")) == 1
    assert records(log, "call") == []
    client.close()
    assert_process_stopped(pid)


def test_pinned_version_rejection_never_launches_mcp_or_sends_commands(managed_factory):
    client, log = managed_factory("wrong-version")

    with pytest.raises(AdapterError):
        client.start()

    assert len(records(log, "version")) == 1
    assert records(log, "started") == []
    assert records(log, "call") == []


@pytest.mark.parametrize("scenario", ["missing-capability", "missing-tool", "missing-schema"])
def test_startup_requires_tools_capability_and_required_tools(managed_factory, scenario):
    client, log = managed_factory(scenario)

    with pytest.raises(AdapterError):
        client.start()

    assert records(log, "call") == []
    with pytest.raises(AdapterError) as error:
        client.call("doc_new", {"marker": "must-not-execute"}, mutating=True)
    assert error.value.code == "connection_unavailable"
    assert records(log, "call") == []


def test_unapproved_tool_is_rejected_before_dispatch(managed_factory):
    client, log = managed_factory()
    client.start()

    with pytest.raises(AdapterError) as error:
        client.call("control_call", {"method": "app.quit"}, mutating=True)

    assert error.value.code == "unsupported_tool"
    assert records(log, "call") == []
    result = client.call("doc_inspect", {"marker": "still-available"})
    assert "still-available" in json.dumps(result)


@pytest.mark.parametrize(
    "scenario",
    [
        "slow",
        "wrong-id",
        "missing-id",
        "stale-id",
        "process-exit",
        "wrong-then-correct",
        "stale-then-correct",
    ],
)
def test_transport_failure_poisoning_never_replays_mutation(managed_factory, scenario):
    client, log = managed_factory(scenario, timeout_seconds=0.4)
    client.start()
    client.call("doc_inspect", {"marker": "known-good-before-fault"})

    with pytest.raises(AdapterError) as failure:
        client.call("doc_new", {"marker": "fault"}, mutating=True)

    assert failure.value.indeterminate is True
    with pytest.raises(AdapterError) as unavailable:
        client.call("doc_inspect", {"marker": "must-not-see-late-reply"})
    assert unavailable.value.code == "connection_unavailable"
    with pytest.raises(AdapterError) as retry:
        client.call("doc_new", {"marker": "must-not-replay"}, mutating=True)
    assert retry.value.code == "connection_unavailable"
    calls = records(log, "call")
    assert [(row["name"], row["arguments"]["marker"]) for row in calls] == [
        ("doc_inspect", "known-good-before-fault"),
        ("doc_new", "fault"),
    ]
    if scenario.endswith("-then-correct"):
        frames = records(log, "paired_replies")
        assert len(frames) == 1
        assert frames[0]["ids"][0] != calls[-1]["request_id"]
        assert frames[0]["ids"][1] == calls[-1]["request_id"]
    assert len(records(log, "started")) == 1
    assert_process_stopped(records(log, "started")[0]["pid"])
