"""Composition root using the public Core ownership and execution seams."""

from pathlib import Path

from dcc_mcp_core import (
    AdapterReadinessBinder,
    DccServerBase,
    DccServerOptions,
    HostExecutionBridge,
)

from . import PHOTOCRAFT_VERSION, __version__
from .facade import ACTIVE_FACADE, PhotoCraftFacade
from .transport import ManagedPhotoCraft


class PhotoCraftDispatcher:
    """Bind a facade to Core's script runner for one admitted call.

    Core owns loading and script execution. The official MCP client owns the
    serialized external engine process; this class only supplies call context.
    """

    def __init__(self, facade):
        self.facade = facade

    def dispatch_callable(self, func, **metadata):
        token = ACTIVE_FACADE.set(self.facade)
        try:
            return func()
        finally:
            ACTIVE_FACADE.reset(token)


class PhotoCraftServer(DccServerBase):
    def __init__(
        self, executable, workspace, *, port=None, gateway_port=0, timeout_seconds=30, **options
    ):
        self.photocraft = ManagedPhotoCraft(executable, workspace, timeout_seconds)
        self.facade = PhotoCraftFacade(self.photocraft)
        bridge = HostExecutionBridge(dispatcher=PhotoCraftDispatcher(self.facade))
        config = DccServerOptions.from_env(
            "photocraft",
            Path(__file__).parent / "skills",
            port=port,
            gateway_port=gateway_port,
            server_name="PhotoCraft experimental adapter",
            server_version=__version__,
            adapter_version=__version__,
            instance_type="standalone",
            execution_bridge=bridge,
            **options,
        )
        super().__init__(options=config)
        self.register_quit_hook(self.photocraft.close)
        self.readiness = AdapterReadinessBinder.bind_headless(
            self, dcc_ready_probe=lambda: self.photocraft.status()["state"] == "connected"
        )
        self.photocraft.on_state_change = self.readiness.refresh_dcc_ready
        self.register_builtin_actions(include_bundled=False)

    def _version_string(self):
        return PHOTOCRAFT_VERSION

    def start(self, **kwargs):
        self.photocraft.start()
        self.readiness.refresh_dcc_ready()
        try:
            return super().start(**kwargs)
        except BaseException:
            self.photocraft.close()
            raise

    def stop(self):
        try:
            super().stop()
        finally:
            self.photocraft.close()
