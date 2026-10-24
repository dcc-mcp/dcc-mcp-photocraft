"""Discover official launcher installs without invoking a host or installer."""

from __future__ import annotations

import json

import pytest

from dcc_mcp_photocraft import host, launcher


@pytest.mark.parametrize("layout", ["portable", "tree", "bundle"])
def test_manifest_discovers_native_cli_without_execution(monkeypatch, tmp_path, layout):
    data = tmp_path / "launcher-data"
    data.mkdir()
    install = tmp_path / "custom-install"
    if layout == "portable":
        install.mkdir()
        recorded = install / "photocraft.exe"
        recorded.touch()
        binaries = install
    elif layout == "bundle":
        recorded = install / "PhotoCraft.app"
        binaries = recorded / "Contents/MacOS"
        binaries.mkdir(parents=True)
    else:
        recorded = install
        binaries = install / "bin"
        binaries.mkdir(parents=True)
    cli = binaries / ("photocraft-cli" + host._SUFFIXES[0])
    cli.touch()
    cli.chmod(0o700)
    manifest = data / "installed.json"
    manifest.write_text(json.dumps({"apps": {"photocraft": {"path": str(recorded)}}}), encoding="utf-8")
    original = manifest.read_bytes()
    monkeypatch.setattr(launcher, "data_directories", lambda: [data])
    monkeypatch.setattr(host, "_which", lambda _: None)
    monkeypatch.setattr(host, "_search_dirs", launcher.search_directories)
    monkeypatch.delenv("PHOTOCRAFT_CLI", raising=False)
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: pytest.fail("discovery executed a binary"))

    assert host.discover().cli == cli
    assert manifest.read_bytes() == original


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"apps":[]}',
        '{"apps":{"photocraft":{"path":"relative/bin"}}}',
        '{"apps":{"photocraft":{"path":42}}}',
    ],
)
def test_bad_launcher_manifest_is_only_an_absent_hint(monkeypatch, tmp_path, payload):
    (tmp_path / "installed.json").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(launcher, "data_directories", lambda: [tmp_path])
    assert launcher.search_directories() == [tmp_path / "apps/photocraft/bin", tmp_path / "apps/photocraft"]


def test_manifest_read_is_bounded(monkeypatch, tmp_path):
    (tmp_path / "installed.json").write_bytes(b" " * (launcher.MAX_MANIFEST_BYTES + 1))
    assert launcher._record_path(tmp_path) is None


def test_portable_data_override_and_xdg_location(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setenv("PHOTOCRAFT_LAUNCHER_DATA", str(tmp_path / "portable/data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    directories = launcher.data_directories()
    assert directories[:2] == [tmp_path / "portable/data", tmp_path / "xdg/artcraft-launcher"]


def test_explicit_cli_keeps_priority_over_launcher_hint(monkeypatch, tmp_path):
    configured = tmp_path / "chosen-cli"
    configured.touch()
    monkeypatch.setenv("PHOTOCRAFT_CLI", str(configured))
    monkeypatch.setattr(host, "find_binary", lambda _: pytest.fail("overrode explicit executable"))
    # The GUI remains optional and may still be discovered.
    monkeypatch.setenv("PHOTOCRAFT_APP", str(configured))
    assert host.discover().cli == configured
