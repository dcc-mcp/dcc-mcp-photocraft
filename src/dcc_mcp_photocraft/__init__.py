"""PhotoCraft adapter for DCC-MCP.

PhotoCraft has no embedded Python interpreter, so unlike the Maya, Blender or
Houdini adapters this package never runs inside the host. It drives PhotoCraft
from an external process over two surfaces:

* ``photocraft-cli`` -- headless subprocess; the primary path, and the only one
  that works without a window.
* the desktop app's loopback control channel -- started with
  ``photocraft --control <port>``, authenticated with a bearer token, used only
  for actions that need the live UI.

Both are optional at import time. Nothing here imports a host module, so skill
discovery and ``--help`` work on a machine where PhotoCraft is not installed.
"""

from .__version__ import __version__
from .actions import ADAPTER_ACTIONS
from .server import PhotoCraftMcpServer, start_server, stop_server

__all__ = [
    "ADAPTER_ACTIONS",
    "PhotoCraftMcpServer",
    "__version__",
    "start_server",
    "stop_server",
]
