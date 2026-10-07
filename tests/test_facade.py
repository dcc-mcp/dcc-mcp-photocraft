"""Unit regressions for facade dispatch; these are not live host E2E tests."""

from types import SimpleNamespace

import pytest

from dcc_mcp_photocraft.facade import PhotoCraftFacade


@pytest.mark.parametrize("tool", ["document_new", "layer_create"])
def test_tool_name_does_not_conflict_with_named_entity(tool, monkeypatch):
    facade = PhotoCraftFacade(SimpleNamespace(workspace=None))
    received = {}

    def named_operation(**arguments):
        received.update(arguments)
        return {"entity_name": arguments["name"]}

    monkeypatch.setattr(facade, tool, named_operation)
    result = facade.invoke(tool, name="Named entity")

    assert result["success"] is True
    assert result["context"]["entity_name"] == "Named entity"
    assert received == {"name": "Named entity"}


def test_malformed_readback_after_edit_remains_indeterminate(monkeypatch):
    facade = PhotoCraftFacade(SimpleNamespace(workspace=None))

    def edited_then_bad_readback(**arguments):
        facade._mutated = True
        raise TypeError("malformed native dimensions")

    monkeypatch.setattr(facade, "document_resize", edited_then_bad_readback)
    result = facade.invoke("document_resize", width=10, height=10)
    assert result["success"] is False
    assert result["error"] == "readback_failed"
    assert result["_meta"]["photocraft"]["indeterminate"] is True


def test_mandatory_readback_survives_cancel_after_edit(monkeypatch):
    from dcc_mcp_photocraft import facade as module

    calls = []
    cancel_checks = []

    def check():
        cancel_checks.append(True)
        if calls:
            raise RuntimeError("cancel after admitted edit")

    def call(name, arguments, **kwargs):
        calls.append(name)
        value = "{}" if name == "command_run" else '{"width": 16, "height": 16}'
        return {"content": [{"type": "text", "text": value}]}

    facade = PhotoCraftFacade(SimpleNamespace(workspace=None, call=call))
    monkeypatch.setattr(module, "check_dcc_cancelled", check)
    result = facade.invoke("undo")
    assert result["success"] is True
    assert calls == ["command_run", "doc_inspect"]
    assert len(cancel_checks) == 2  # invocation + pre-admission, not post-edit


def test_text_budget_uses_native_resolution_before_dispatch(monkeypatch):
    facade = PhotoCraftFacade(SimpleNamespace(workspace=None))
    monkeypatch.setattr(facade, "_layer_budget", lambda: {"resolution": 1200})

    def unexpected_edit(*args):
        pytest.fail("Oversized text reached the engine")

    monkeypatch.setattr(facade, "_edit", unexpected_edit)
    result = facade.invoke("text_create", text="A" * 256, x=0, y=0, size=128)
    assert result["success"] is False
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False
