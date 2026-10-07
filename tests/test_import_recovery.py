"""Failure admission tests; fixture responses are not software E2E evidence."""

import copy
import json
from types import SimpleNamespace

import pytest

from dcc_mcp_photocraft.errors import AdapterError
from dcc_mcp_photocraft.facade import PhotoCraftFacade
from dcc_mcp_photocraft.paths import Workspace


def document(name, identity):
    return {
        "name": name,
        "width": 16,
        "height": 16,
        "revision": 0,
        "activeLayer": identity,
        "selectedLayers": [identity],
        "hasSelection": False,
        "channels": [],
        "quickMask": False,
        "layers": [
            {
                "id": identity,
                "kind": "Pixel",
                "bounds": [0, 0, 16, 16],
                "hasMask": False,
                "linkGroup": None,
            }
        ],
    }


class ImportHost:
    def __init__(self, fail_at=None, lose_paste_reply=False, wrong_target=False):
        self.fail_at = fail_at
        self.lose_paste_reply = lose_paste_reply
        self.wrong_target = wrong_target
        self.calls = []
        self.active = 0
        self.documents = [document("Target", 1)]
        self.source = {"path": "source.png", "sha256": "fixture", "dimensions": [16, 16]}
        self.workspace = SimpleNamespace(
            stage_input=lambda _: ".imports/source.png", manifest=lambda: {"inputs": [self.source]}
        )

    def call(self, name, arguments, **kwargs):
        operation = arguments["id"] if name == "command_run" else name
        self.calls.append((operation, arguments, kwargs))
        if operation == self.fail_at:
            raise AdapterError("fixture_timeout", indeterminate=kwargs.get("mutating", False))
        if name == "session_list":
            value = {
                "active": self.active,
                "documents": [
                    {"index": i, "name": d["name"]} for i, d in enumerate(self.documents)
                ],
            }
        elif name == "doc_inspect":
            value = copy.deepcopy(self.documents[self.active])
        elif name == "doc_open":
            self.documents.append(document("Source", 2))
            self.active = 1
            value = {}
        elif name == "doc_select":
            self.active = arguments["index"]
            if self.wrong_target:
                self.documents[self.active]["name"] = "Other target"
            value = {}
        elif name == "doc_close":
            assert arguments["index"] == 1, "Only the newly opened temporary source may close"
            self.documents.pop(1)
            value = {}
        elif operation == "edit.copy":
            value = {"bounds": [0, 0, 16, 16]}
        elif operation == "edit.paste":
            layer = copy.deepcopy(self.documents[1]["layers"][0])
            layer["id"] = 3
            self.documents[0]["layers"].insert(0, layer)
            self.documents[0]["activeLayer"] = 3
            self.documents[0]["selectedLayers"] = [3]
            self.documents[0]["revision"] += 1
            if self.lose_paste_reply:
                raise AdapterError("fixture_timeout", indeterminate=True)
            value = {"layer": 3}
        elif operation == "layer.setProps":
            self.documents[0]["layers"][0]["name"] = arguments["params"]["name"]
            value = None
        else:
            assert operation == "edit.purge.clipboard"
            value = None
        return {"content": [{"type": "text", "text": json.dumps(value)}]}


@pytest.mark.parametrize(
    "stage",
    [
        "doc_open",
        "edit.copy",
        "doc_select",
        "edit.paste",
        "layer.setProps",
        "doc_close",
        "edit.purge.clipboard",
    ],
)
def test_partial_import_never_replays_or_blindly_closes_user_documents(stage):
    host = ImportHost(stage)
    facade = PhotoCraftFacade(host)

    result = facade.invoke("layer_import", path="source.png", name="Imported")

    assert result["success"] is False
    assert result["_meta"]["photocraft"]["indeterminate"] is True
    operations = [name for name, _, _ in host.calls]
    assert operations.count(stage) == 1
    assert operations[-1] == stage
    assert host.documents[0]["name"] == "Target"
    count = len(host.calls)
    blocked = facade.invoke("layer_import", path="source.png")
    assert blocked["error"] == "session_requires_recovery"
    assert len(host.calls) == count


def test_import_success_closes_only_its_temporary_document_and_purges_clipboard():
    host = ImportHost()
    result = PhotoCraftFacade(host).invoke("layer_import", path="source.png")
    assert result["success"] is True
    assert result["context"]["layer"] == 3
    assert len(host.documents) == 1
    assert host.active == 0
    operations = [name for name, _, _ in host.calls]
    assert operations.count("edit.paste") == 1
    assert operations.index("doc_close") < operations.index("edit.purge.clipboard")


@pytest.mark.parametrize("format,suffix", [("PNG", ".jpg"), ("GIF", ".png")])
def test_staged_image_format_must_match_the_typed_input_extension(tmp_path, format, suffix):
    from PIL import Image

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    Image.new("RGB", (2, 2)).save(inputs / ("input" + suffix), format=format)
    workspace = Workspace(inputs, tmp_path / "outputs")
    with pytest.raises(AdapterError, match="unsupported_image_format"):
        workspace.stage_input("input" + suffix)
    assert not list(workspace.import_root.iterdir())


def test_animated_png_is_rejected_before_the_native_decoder(tmp_path):
    from PIL import Image

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    Image.new("RGBA", (2, 2), "red").save(
        inputs / "animated.png", save_all=True, append_images=[Image.new("RGBA", (2, 2), "blue")]
    )
    workspace = Workspace(inputs, tmp_path / "outputs")
    with pytest.raises(AdapterError, match="unsupported_image_format"):
        workspace.stage_input("animated.png")


def test_lost_paste_reply_preserves_applied_layer_without_replay_or_cleanup():
    host = ImportHost(lose_paste_reply=True)
    facade = PhotoCraftFacade(host)
    result = facade.invoke("layer_import", path="source.png")
    assert result["_meta"]["photocraft"]["indeterminate"] is True
    assert len(host.documents[0]["layers"]) == 2  # Engine already edited the target.
    assert len(host.documents) == 2  # No speculative cleanup/close after unknown outcome.
    operations = [name for name, _, _ in host.calls]
    assert operations.count("edit.paste") == 1
    assert not {"edit.undo", "doc_close", "edit.purge.clipboard"}.intersection(operations)
    count = len(host.calls)
    assert facade.invoke("layer_import", path="source.png")["error"] == "session_requires_recovery"
    assert len(host.calls) == count


def test_wrong_target_readback_prevents_paste():
    host = ImportHost(wrong_target=True)
    result = PhotoCraftFacade(host).invoke("layer_import", path="source.png")
    assert result["_meta"]["photocraft"]["indeterminate"] is True
    assert "edit.paste" not in [name for name, _, _ in host.calls]


def test_long_thin_image_is_rejected_before_native_open(tmp_path):
    from PIL import Image

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    Image.new("RGB", (4097, 1)).save(inputs / "wide.png")
    workspace = Workspace(inputs, tmp_path / "outputs")
    with pytest.raises(AdapterError, match="resource_limit"):
        workspace.stage_input("wide.png")
