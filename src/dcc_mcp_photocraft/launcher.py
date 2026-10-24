"""Read the official Craft Launcher's install locations without launching it.

The launcher owns downloads, verification, updates and removal. Its manifest is
only a discovery hint, never proof of a trusted binary or a running GUI.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENV_LAUNCHER_DATA = "PHOTOCRAFT_LAUNCHER_DATA"
MAX_MANIFEST_BYTES = 1024 * 1024


def data_directories() -> list[Path]:
    """Return known data folders and an optional operator-selected portable one."""
    home = Path.home()
    configured = os.environ.get(ENV_LAUNCHER_DATA)
    directories = [Path(configured)] if configured and Path(configured).is_absolute() else []
    if sys.platform == "win32":
        directories.append(Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "ArtCraft Launcher")
    elif sys.platform == "darwin":
        directories.append(home / "Library/Application Support/ArtCraft Launcher")
    else:
        directories.extend(
            [
                Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "artcraft-launcher",
                home / ".var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher",
            ]
        )
    return directories


def _record_path(directory: Path) -> Path | None:
    try:
        # Bound the actual read too: stat alone cannot bound a growing file.
        with (directory / "installed.json").open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            return None
        manifest = json.loads(raw)
        record = manifest["apps"]["photocraft"]
        value = record["path"]
        if not isinstance(value, str) or not value or "\x00" in value:
            return None
        path = Path(value)
        return path if path.is_absolute() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def search_directories() -> list[Path]:
    """Find native trees, portable siblings and macOS bundles from the manifest.

    AppImages and Flatpak application IDs are not CLI binaries. No package is
    extracted and no shell command, package manager or executable is invoked.
    """
    found: list[Path] = []
    for directory in data_directories():
        recorded = _record_path(directory)
        if recorded is not None:
            if recorded.name.lower() in {"photocraft", "photocraft.exe"}:
                found.append(recorded.parent)
            elif recorded.name == "PhotoCraft.app":
                found.append(recorded / "Contents/MacOS")
            elif recorded.is_dir():
                found.extend([recorded / "bin", recorded])
        # These are the launcher's default portable/tarball tree locations.
        tree = directory / "apps/photocraft"
        found.extend([tree / "bin", tree])
    return list(dict.fromkeys(found))
