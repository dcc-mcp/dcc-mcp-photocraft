"""Tests for the server lifecycle.

The two contracts here are about failure, not the happy path. A start that fails
halfway must not leave a thread running, and it must not leave an instance that
later callers will be handed as though it were live.
"""

from __future__ import annotations

import pytest

from dcc_mcp_photocraft import server as server_module


@pytest.fixture
def no_real_host(monkeypatch):
    """Keep the constructor from touching a real host or opening a port.

    The readiness binder is stubbed as a whole: its real signature takes the
    server as its first positional argument, and reproducing it here would only
    couple these tests to Core's argument order.
    """
    monkeypatch.setattr(server_module.PhotoCraftMcpServer, "_host_ready", lambda self: True)
    monkeypatch.setattr(server_module, "discover", lambda: None)

    class FakeReadiness:
        def __init__(self, *args, **kwargs):
            pass

        def bind_queue_dispatcher(self, *args, **kwargs):
            return self

        def mark_execution_ready(self, *args, **kwargs):
            return self

        def mark_dispatcher_ready(self, *args, **kwargs):
            return self

        def refresh_dcc_ready(self, *args, **kwargs):
            return True

        def publish(self, *args, **kwargs):
            return True

    monkeypatch.setattr(server_module, "AdapterReadinessBinder", FakeReadiness)
    return monkeypatch


def _instance(monkeypatch):
    """Build a server with the base-class lifecycle stubbed out."""
    calls = {"start": 0, "stop": 0}
    monkeypatch.setattr(
        server_module.DccServerBase,
        "start",
        lambda self, **kwargs: calls.__setitem__("start", calls["start"] + 1),
    )
    monkeypatch.setattr(
        server_module.DccServerBase,
        "stop",
        lambda self, **kwargs: calls.__setitem__("stop", calls["stop"] + 1),
    )
    return calls


def test_failed_start_stops_the_host_driver(no_real_host, monkeypatch):
    """A start that raises must not leave the host thread running."""
    started = []
    stopped = []

    class FakeDriver:
        def start(self):
            started.append(True)

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(server_module, "StandaloneHost", lambda *a, **k: FakeDriver())
    monkeypatch.setattr(
        server_module.DccServerBase,
        "start",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("port in use")),
    )

    instance = server_module.PhotoCraftMcpServer(gateway_port=0)

    with pytest.raises(RuntimeError, match="port in use"):
        instance.start()

    assert started == [True]
    # The driver outlives the exception without this, and a later start would
    # run against a readiness state that was never published.
    assert stopped == [True]


def test_start_server_does_not_cache_a_failed_instance(no_real_host, monkeypatch):
    """A server that failed to start must not be returned as running."""
    _instance(monkeypatch)
    monkeypatch.setattr(
        server_module.PhotoCraftMcpServer,
        "start",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("port in use")),
    )
    monkeypatch.setattr(server_module, "_server", None)

    with pytest.raises(RuntimeError, match="port in use"):
        server_module.start_server()

    # Caching it would make every later call return the dead instance, which
    # capability_dispatch would then treat as the running server.
    assert server_module._server is None


def test_start_server_caches_a_started_instance(no_real_host, monkeypatch):
    """The happy path still assigns the global."""
    _instance(monkeypatch)
    monkeypatch.setattr(server_module.PhotoCraftMcpServer, "start", lambda self, **kwargs: None)
    monkeypatch.setattr(server_module, "_server", None)

    started = server_module.start_server()

    assert server_module._server is started
    server_module.stop_server()
    assert server_module._server is None
