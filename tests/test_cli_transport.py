"""Tests for the headless ``photocraft-cli`` transport.

These use a stub binary instead of a real PhotoCraft install, so they run in CI
without Rust and without the app. What they pin down is the contract on this
side of the process boundary: argument construction, exit-code handling, and
JSON parsing.
"""

from __future__ import annotations

import json
import shlex
import stat
import sys
from pathlib import Path

import pytest

from dcc_mcp_photocraft.cli_transport import CliError, CliNotFound, CliTransport


def _write_stub(tmp_path: Path, lines: list[str]) -> Path:
    """Write an executable stub that prints ``lines`` to stdout, one per line.

    Lines are taken literally rather than as shell syntax so the same fixture
    works on Windows, where the stub is a batch file, and on POSIX, where it is
    a shell script.
    """
    if sys.platform == "win32":
        path = tmp_path / "photocraft-cli.bat"
        body = "@echo off\r\n" + "".join(f"echo {line}\r\n" for line in lines)
        path.write_text(body, encoding="utf-8")
    else:
        path = tmp_path / "photocraft-cli"
        body = "#!/bin/sh\n" + "".join(f"printf '%s\\n' {shlex.quote(line)}\n" for line in lines)
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _echo_stub(tmp_path: Path, payload: str) -> Path:
    """A stub that prints one fixed line, ignoring its arguments."""
    return _write_stub(tmp_path, [payload])


def test_missing_binary_raises_not_found(tmp_path):
    with pytest.raises(CliNotFound):
        CliTransport(tmp_path / "does-not-exist")


def test_commands_parses_json_array(tmp_path):
    stub = _echo_stub(tmp_path, '[{"id":"file.new"},{"id":"layer.new.layer"}]')

    assert CliTransport(stub).commands() == [{"id": "file.new"}, {"id": "layer.new.layer"}]


def test_commands_parses_object_with_commands_key(tmp_path):
    stub = _echo_stub(tmp_path, '{"commands":[{"id":"file.open"}]}')

    assert CliTransport(stub).commands() == [{"id": "file.open"}]


def test_commands_tolerates_an_unexpected_shape(tmp_path):
    """An unrecognised payload yields an empty table rather than a crash."""
    stub = _echo_stub(tmp_path, '{"unexpected":true}')

    assert CliTransport(stub).commands() == []


def test_non_zero_exit_is_reported(tmp_path):
    if sys.platform == "win32":
        path = tmp_path / "photocraft-cli.bat"
        path.write_text("@echo off\r\necho boom 1>&2\r\nexit /b 3\r\n", encoding="utf-8")
    else:
        path = tmp_path / "photocraft-cli"
        path.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(CliError, match="exit 3"):
        CliTransport(path).run(["info", "x.pcraft"])


def test_empty_output_raises(tmp_path):
    stub = _echo_stub(tmp_path, "")

    with pytest.raises(CliError, match="no JSON|no output"):
        CliTransport(stub).commands()


def test_read_and_write_roots_are_forwarded_separately(tmp_path, monkeypatch):
    """The two roots are independent authorities, so each is forwarded alone."""
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(list(command))
        import subprocess

        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    CliTransport(stub, read_root="/in").commands()
    assert "--automation-read-root" in captured[0]
    assert "--automation-write-root" not in captured[0]

    CliTransport(stub, read_root="/in", write_root="/out").commands()
    assert "--automation-read-root" in captured[1]
    assert "--automation-write-root" in captured[1]
    assert captured[1][captured[1].index("--automation-write-root") + 1] == "/out"

    CliTransport(stub).commands()
    assert "--automation-read-root" not in captured[2]
    assert "--automation-write-root" not in captured[2]


def test_run_command_passes_the_command_and_params(tmp_path, monkeypatch):
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(list(command))
        import subprocess

        return subprocess.CompletedProcess(command, 0, stdout='{"command":"file.new","result":{}}', stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    result = CliTransport(stub).run_command("file.new", {"width": 800, "height": 600})

    assert result == {"command": "file.new", "result": {}}
    args = captured[0]
    assert "--cmd" in args
    assert args[args.index("--cmd") + 1] == "file.new"
    # Params travel as one JSON object through ``--params``, so nested
    # structures survive the command line intact.
    assert "--params" in args
    assert json.loads(args[args.index("--params") + 1]) == {"width": 800, "height": 600}


def test_methods_posts_a_json_lines_request_to_serve(tmp_path, monkeypatch):
    """``serve`` is a session, so the request goes to stdin, not argv."""
    import subprocess

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["stdin"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"id":1,"ok":true,"result":["engine.execute","doc.open","methods"]}',
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    methods = CliTransport(stub).methods()

    assert methods == ["engine.execute", "doc.open", "methods"]
    assert "serve" in captured["command"]
    # The request is written to the process's stdin as one JSON line.
    assert json.loads(captured["stdin"])["method"] == "methods"


def test_methods_reports_an_error_reply(tmp_path, monkeypatch):
    """``serve`` reports failures with ok=false, like the control channel."""
    import subprocess

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout='{"id":1,"ok":false,"error":"unknown method"}', stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    with pytest.raises(CliError, match="unknown method"):
        CliTransport(stub).methods()


def test_methods_detects_an_id_mismatch(tmp_path, monkeypatch):
    import subprocess

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout='{"id":99,"ok":true,"result":[]}', stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    with pytest.raises(CliError, match="does not match"):
        CliTransport(stub).methods()


def test_json_is_recovered_from_surrounding_output(tmp_path):
    """Upstream writes progress lines around the payload in some subcommands."""
    stub = _write_stub(tmp_path, ["opening document...", '{"layers": 3}'])

    assert CliTransport(stub).info("x.pcraft") == {"layers": 3}


def test_timeout_reports_the_bound_that_applied(tmp_path, monkeypatch):
    """A per-call override replaces the default, and the message must say so."""
    import subprocess

    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr("subprocess.run", timed_out)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    with pytest.raises(CliError, match="timed out after 5.0s"):
        CliTransport(stub).run(["commands"], timeout_secs=5.0)


def test_serve_reports_a_non_zero_exit(tmp_path, monkeypatch):
    import subprocess

    def failing(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    monkeypatch.setattr("subprocess.run", failing)
    stub = tmp_path / "photocraft-cli"
    stub.write_text("", encoding="utf-8")

    with pytest.raises(CliError, match="exit 1"):
        CliTransport(stub).methods()
