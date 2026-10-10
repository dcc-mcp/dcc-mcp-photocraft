"""Locating and probing the PhotoCraft binaries on the local machine.

PhotoCraft has no embedded Python interpreter, so the adapter always runs in its
own process and drives the host from the outside. Two host entry points matter:

* ``photocraft-cli`` -- headless; drives one engine session with no window.
* ``photocraft`` -- the desktop app; starts a control channel with
  ``--control <port>``.

Neither is installed into a fixed location, and upstream publishes them inside
the GUI packages and as standalone archives. Detection therefore searches
``PATH`` first and then per-platform install directories, which is what keeps
this working whether the user installed a package or unpacked an archive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CLI_BINARY = "photocraft-cli"
APP_BINARY = "photocraft"

if sys.platform == "win32":
    _SUFFIXES = (".exe", ".cmd", ".bat")
else:
    _SUFFIXES = ("",)


@dataclass(frozen=True)
class HostBinaries:
    """The resolved PhotoCraft executables, when present."""

    cli: Path | None
    app: Path | None


def _candidate_names(binary: str) -> list[str]:
    return [binary + suffix for suffix in _SUFFIXES]


def _which(binary: str) -> Path | None:
    for name in _candidate_names(binary):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _search_dirs() -> list[Path]:
    """Per-platform directories where PhotoCraft packages install the binaries."""
    home = Path.home()
    if sys.platform == "win32":
        roots = [
            Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")),
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")),
        ]
        return [root / "PhotoCraft" for root in roots] + [root / "PhotoCraft" / "bin" for root in roots]

    if sys.platform == "darwin":
        return [
            Path("/Applications/PhotoCraft.app/Contents/MacOS"),
            home / "Applications/PhotoCraft.app/Contents/MacOS",
            Path("/usr/local/bin"),
        ]

    return [Path("/usr/bin"), Path("/usr/local/bin"), home / ".local" / "bin"]


def _find_in_search_dirs(binary: str) -> Path | None:
    for directory in _search_dirs():
        for name in _candidate_names(binary):
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def find_binary(binary: str) -> Path | None:
    """Resolve one PhotoCraft executable, or return ``None`` when it is absent."""
    return _which(binary) or _find_in_search_dirs(binary)


def discover() -> HostBinaries:
    """Locate both PhotoCraft executables without invoking either of them.

    Detection is a filesystem lookup only: nothing here spawns a subprocess,
    so it stays safe to call from skill discovery and from ``--help``.
    """
    return HostBinaries(cli=find_binary(CLI_BINARY), app=find_binary(APP_BINARY))


def cli_version(cli: Path | None = None) -> str | None:
    """Return the version reported by ``photocraft-cli --version``.

    ``None`` means the version could not be determined, which is not an error:
    the binary may be an older build without the flag.
    """
    resolved = cli or find_binary(CLI_BINARY)
    if resolved is None:
        return None
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


@dataclass(frozen=True)
class HostStatus:
    """What the adapter can do with the host right now."""

    binaries: HostBinaries
    version: str | None

    @property
    def cli_available(self) -> bool:
        return self.binaries.cli is not None

    @property
    def app_available(self) -> bool:
        return self.binaries.app is not None

    def describe(self) -> str:
        if not self.cli_available and not self.app_available:
            return "PhotoCraft not found; install it or put photocraft-cli on PATH"
        parts = []
        if self.cli_available:
            parts.append(f"cli={self.binaries.cli}")
        if self.app_available:
            parts.append(f"app={self.binaries.app}")
        if self.version:
            parts.append(f"version={self.version}")
        return ", ".join(parts)


def probe(cli: Path | None = None) -> HostStatus:
    """Report the host binaries and version without starting the app."""
    binaries = discover()
    return HostStatus(binaries=binaries, version=cli_version(cli or binaries.cli))
