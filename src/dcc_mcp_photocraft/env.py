"""Environment variables the adapter honours.

This module exists so that the transport layer can read configuration without
importing the server: the server imports the transports, so a transport that
imported the server back would be circular. Keeping the names here lets both
sides share one definition.
"""

from __future__ import annotations

import os

# Path to the ``photocraft-cli`` binary, for installs where it is not on PATH.
ENV_CLI = "PHOTOCRAFT_CLI"

# Path to the desktop app binary, which hosts the control channel.
ENV_APP = "PHOTOCRAFT_APP"

# The port the running app's control channel listens on. PhotoCraft takes this
# as ``--control <port>`` at launch rather than publishing it to a file, so the
# operator who starts the app also passes the same number here.
ENV_CONTROL_PORT = "PHOTOCRAFT_CONTROL_PORT"

# The bearer token for the control channel, or a file holding it. Upstream
# accepts either; the file form keeps the credential off the process command
# line.
ENV_CONTROL_TOKEN = "PHOTOCRAFT_CONTROL_TOKEN"
ENV_CONTROL_TOKEN_FILE = "PHOTOCRAFT_CONTROL_TOKEN_FILE"

# Optional sandbox roots forwarded to ``photocraft-cli``.
ENV_READ_ROOT = "PHOTOCRAFT_READ_ROOT"
ENV_WRITE_ROOT = "PHOTOCRAFT_WRITE_ROOT"


def cli_path() -> str | None:
    """Return the configured CLI path, or ``None`` to fall back to discovery."""
    return os.environ.get(ENV_CLI)


def app_path() -> str | None:
    """Return the configured desktop app path, or ``None``."""
    return os.environ.get(ENV_APP)


def control_port() -> str | None:
    """Return the configured control port, or ``None``."""
    return os.environ.get(ENV_CONTROL_PORT)


def control_token() -> str | None:
    """Return the configured control token, or ``None``."""
    return os.environ.get(ENV_CONTROL_TOKEN)


def control_token_file() -> str | None:
    """Return the path to a file holding the control token, or ``None``."""
    return os.environ.get(ENV_CONTROL_TOKEN_FILE)


def read_root() -> str | None:
    """Return the configured automation read root, or ``None``."""
    return os.environ.get(ENV_READ_ROOT)


def write_root() -> str | None:
    """Return the configured automation write root, or ``None``."""
    return os.environ.get(ENV_WRITE_ROOT)
