"""Bounded editing regressions using metadata only; no native process or large images."""

from types import SimpleNamespace

import pytest

from dcc_mcp_photocraft.errors import AdapterError
from dcc_mcp_photocraft.facade import PhotoCraftFacade
from dcc_mcp_photocraft.paths import MAX_PIXELS
from dcc_mcp_photocraft.safety import budget, geometry, layers, resize_budget


def pixel(layer_id=1, bounds=(0, 0, 32, 32), **properties):
    return {
        "id": layer_id,
        "kind": "Pixel",
        "name": f"Layer {layer_id}",
        "bounds": list(bounds) if bounds is not None else None,
        "hasMask": False,
        "linkGroup": None,
        **properties,
    }


def document(*document_layers, width=64, height=64, **properties):
    return {
        "width": width,
        "height": height,
        "mode": "Rgb",
        "depth": 8,
        "resolution": 72,
        "layers": list(document_layers) if document_layers else [pixel()],
        "activeLayer": 1,
        "selectedLayers": [1],
        "hasSelection": False,
        "channels": {"alpha": [], "quickMask": None, "target": {"kind": "composite"}},
        "quickMask": False,
        **properties,
    }


def facade_for(monkeypatch, doc):
    facade = PhotoCraftFacade(SimpleNamespace(workspace=None))
    monkeypatch.setattr(facade, "_inspect", lambda: doc)
    return facade


def no_native_edit(*args, **kwargs):
    pytest.fail("Rejected request reached native mutation")


def test_layer_inventory_preserves_nested_parent_identity():
    child = pixel(3)
    group = pixel(2, kind="Group", bounds=None, children=[child])
    doc = document(pixel(1), group)

    assert [(layer["id"], parent) for layer, parent in layers(doc)] == [
        (1, None),
        (2, None),
        (3, 2),
    ]


def test_layer_budget_counts_children_and_reserves_the_next_layer():
    group = pixel(
        1, bounds=None, kind="Group", children=[pixel(i, [0, 0, 1, 1]) for i in range(2, 65)]
    )
    doc = document(group)

    assert len(budget(doc)) == 64
    with pytest.raises(AdapterError):
        budget(doc, extra_layers=1)


def test_duplicate_layer_ids_are_not_an_addressable_tree():
    group = pixel(2, bounds=None, kind="Group", children=[pixel(1)])

    with pytest.raises(AdapterError):
        layers(document(pixel(1), group))


@pytest.mark.parametrize("depth,admitted", [(8, True), (9, False)])
def test_nested_depth_limit_is_independent_of_layer_count(depth, admitted):
    nested = pixel(depth)
    for layer_id in reversed(range(1, depth)):
        nested = pixel(layer_id, bounds=None, kind="Group", children=[nested])
    doc = document(nested)

    if admitted:
        assert len(layers(doc)) == depth
    else:
        with pytest.raises(AdapterError):
            layers(doc)


def test_empty_group_at_maximum_depth_does_not_add_another_level():
    nested = pixel(8, bounds=None, kind="Group", children=[])
    for layer_id in reversed(range(1, 8)):
        nested = pixel(layer_id, bounds=None, kind="Group", children=[nested])

    assert len(layers(document(nested))) == 8


def test_single_surface_budget_uses_content_outside_the_canvas():
    doc = document(pixel(bounds=[-4096, -4096, 4097, 4096]), width=1, height=1)

    with pytest.raises(AdapterError):
        budget(doc)


def test_aggregate_budget_counts_all_surface_pixels():
    doc = document(*(pixel(i, [0, 0, 4096, 4096]) for i in range(1, 6)))

    with pytest.raises(AdapterError):
        budget(doc)


def test_aggregate_budget_reserves_import_pixels_before_dispatch():
    doc = document(*(pixel(i, [0, 0, 4096, 4096]) for i in range(1, 5)))

    with pytest.raises(AdapterError):
        budget(doc, extra_pixels=1)


def test_nested_surface_mask_and_selection_share_one_budget():
    group = pixel(
        1,
        bounds=None,
        kind="Group",
        children=[pixel(i, [0, 0, 4096, 4096], hasMask=i == 2) for i in range(2, 5)],
    )
    doc = document(group, width=4096, height=4096)
    assert len(budget(doc)) == 4

    doc.update(hasSelection=True, selectionBounds=[100, 100, 1, 1])
    with pytest.raises(AdapterError):
        budget(doc)


@pytest.mark.parametrize(
    "properties",
    [{"hasMask": True}, {"linkGroup": 7}, {"kind": "Type"}, {"kind": "Adjustment"}],
)
def test_targeted_geometry_rejects_unbounded_or_unsupported_layers(properties):
    with pytest.raises(AdapterError):
        geometry(document(pixel(**properties)), layer=1)


def test_geometry_rejects_hidden_selection_scope():
    with pytest.raises(AdapterError):
        geometry(document(hasSelection=True, selectionBounds=[0, 0, 8, 8]), layer=1)


@pytest.mark.parametrize("properties", [{"hasMask": True}, {"kind": "Type"}])
def test_document_geometry_checks_every_layer(properties):
    with pytest.raises(AdapterError):
        geometry(document(pixel(1), pixel(2, **properties)))


def test_targeted_geometry_requires_an_existing_layer():
    with pytest.raises(AdapterError):
        geometry(document(), layer=999)


def test_known_pixel_geometry_and_modest_resize_are_admitted():
    doc = document(pixel(bounds=[0, 0, 64, 64]))

    geometry(doc, layer=1)
    geometry(doc)
    resize_budget(doc, 128, 128)


def test_native_default_composite_channel_state_allows_geometry():
    doc = document(channels={"alpha": [], "quickMask": None, "target": {"kind": "composite"}})

    geometry(doc, layer=1)
    geometry(doc)
    resize_budget(doc, 128, 128)


@pytest.mark.parametrize(
    "channel_state",
    [
        {"alpha": [{"id": 1}], "quickMask": None, "target": {"kind": "composite"}},
        {"alpha": [], "quickMask": None, "target": {"kind": "channel", "index": 0}},
        {"alpha": [], "quickMask": {"name": "Quick Mask"}, "target": {"kind": "composite"}},
    ],
)
def test_native_auxiliary_or_noncomposite_channels_block_geometry(channel_state):
    doc = document(channels=channel_state)

    with pytest.raises(AdapterError):
        geometry(doc, layer=1)
    with pytest.raises(AdapterError):
        resize_budget(doc, 128, 128)


def test_small_off_canvas_content_can_be_resized_safely():
    resize_budget(document(pixel(bounds=[-16, -16, 32, 32])), 128, 128)


@pytest.mark.parametrize(
    "invalid_bounds",
    [[0, 0, -1, 32], [0, 0, True, 32], [0, 0, float("inf"), 32], [0, 0, 32]],
)
def test_malformed_native_bounds_fail_before_resizing(invalid_bounds):
    with pytest.raises(AdapterError):
        resize_budget(document(pixel(bounds=invalid_bounds)), 128, 128)


def test_resize_limits_combined_surfaces_even_when_each_surface_fits():
    doc = document(*(pixel(i, [0, 0, 2048, 2048]) for i in range(1, 6)))
    budget(doc)

    with pytest.raises(AdapterError):
        resize_budget(doc, 128, 128)


def test_resize_uses_hidden_surface_scale_before_native_allocation(monkeypatch):
    doc = document(pixel(bounds=[0, 0, 4096, 4096]), width=1, height=1)
    facade = facade_for(monkeypatch, doc)
    monkeypatch.setattr(facade, "_edit", no_native_edit)

    result = facade.invoke("document_resize", width=4096, height=4096)

    assert result["success"] is False
    assert result["_meta"]["photocraft"]["indeterminate"] is False


@pytest.mark.parametrize(
    "doc",
    [
        document(pixel(hasMask=True)),
        document(channels=[{"id": 1, "name": "Hidden alpha"}]),
        document(quickMask=True),
    ],
)
def test_resize_rejects_unobserved_mask_or_channel_geometry(doc):
    with pytest.raises(AdapterError):
        resize_budget(doc, 128, 128)


@pytest.mark.parametrize(
    "rectangle",
    [
        {"x": 63, "y": 0, "width": 2, "height": 1},
        {"x": 0, "y": 63, "width": 1, "height": 2},
        {"x": 0, "y": 0, "width": 65, "height": 64},
    ],
)
def test_selection_rect_rejects_extents_outside_canvas_before_edit(monkeypatch, rectangle):
    facade = facade_for(monkeypatch, document())
    monkeypatch.setattr(facade, "_edit", no_native_edit)

    result = facade.invoke("selection_rect", **rectangle)

    assert result["success"] is False
    assert result["error"] == "selection_outside_canvas"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


def test_selection_feather_budget_includes_pixels_beyond_rectangle(monkeypatch):
    facade = facade_for(monkeypatch, document(width=4096, height=4096))
    monkeypatch.setattr(facade, "_edit", no_native_edit)

    result = facade.invoke("selection_rect", x=0, y=0, width=4096, height=4096, feather=1)

    assert result["success"] is False
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


def test_selection_at_canvas_edge_has_exact_bounds_readback(monkeypatch):
    before = document()
    after = {**before, "hasSelection": True, "selectionBounds": [56, 48, 8, 16]}
    facade = facade_for(monkeypatch, before)
    dispatched = []

    def edit(command, params):
        dispatched.append(command)
        return {"document": after, "readback": True}

    monkeypatch.setattr(facade, "_edit", edit)
    result = facade.invoke("selection_rect", x=56, y=48, width=8, height=16)

    assert result["success"] is True
    assert result["postcondition"]["verified"] is True
    assert dispatched == ["select.rect"]


def test_mask_brush_rejects_long_zigzag_work_before_edit(monkeypatch):
    facade = facade_for(monkeypatch, document(pixel(hasMask=True), width=1024, height=1024))
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    points = [{"x": 1024 * (i % 2), "y": 1024 * (i % 2)} for i in range(256)]

    result = facade.invoke("mask_paint", layer=1, points=points, size=256)

    assert result["success"] is False
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


def test_mask_brush_budget_accounts_for_brush_extent_outside_canvas(monkeypatch):
    facade = facade_for(monkeypatch, document(pixel(hasMask=True), width=4096, height=4096))
    monkeypatch.setattr(facade, "_edit", no_native_edit)

    result = facade.invoke("mask_paint", layer=1, points=[{"x": 4096, "y": 4096}], size=1)

    assert result["success"] is False
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


def test_mask_brush_requires_one_explicit_active_layer(monkeypatch):
    doc = document(pixel(1, hasMask=True), pixel(2), selectedLayers=[1, 2])
    facade = facade_for(monkeypatch, doc)
    monkeypatch.setattr(facade, "_edit", no_native_edit)

    result = facade.invoke("mask_paint", layer=1, points=[{"x": 1, "y": 1}])

    assert result["success"] is False
    assert result["error"] == "single_active_layer_required"


def test_mask_enabled_rejects_inactive_target_before_native_active_layer_gate(monkeypatch):
    doc = document(pixel(1), pixel(2, hasMask=True), activeLayer=1, selectedLayers=[1])
    facade = facade_for(monkeypatch, doc)
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke("mask_enabled", layer=2, enabled=False)
    assert result["success"] is False
    assert result["error"] == "single_active_layer_required"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


@pytest.mark.parametrize(
    "error",
    [AdapterError("postcondition_failed"), TypeError("bad readback"), KeyError("missing readback")],
)
@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("layer_create", {"name": "Next layer"}),
        ("document_session", {"action": "select", "index": 0}),
        ("document_session", {"action": "close", "index": 0}),
        ("preview", {"path": "preview.png"}),
        ("export_image", {"path": "export.png"}),
        ("undo", {}),
    ],
)
def test_unknown_mutation_blocks_later_edits_and_artifact_writes(
    monkeypatch, error, tool, arguments
):
    facade = facade_for(monkeypatch, document())

    def admitted_then_failed(**kwargs):
        facade._mutated = True
        raise error

    monkeypatch.setattr(facade, "document_resize", admitted_then_failed)
    failed = facade.invoke("document_resize", width=128, height=128)
    assert failed["success"] is False
    assert failed["_meta"]["photocraft"]["indeterminate"] is True

    monkeypatch.setattr(facade, tool, no_native_edit)
    blocked = facade.invoke(tool, **arguments)

    assert blocked["success"] is False
    assert blocked["_meta"]["photocraft"]["indeterminate"] is True
    assert blocked["_meta"]["photocraft"]["retry_safe"] is False


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("connection_status", {}),
        ("document_inspect", {}),
        ("capabilities_query", {}),
        ("document_session", {"action": "list"}),
        ("document_session", {}),
    ],
)
def test_unknown_state_allows_diagnostics_without_clearing_the_latch(monkeypatch, tool, arguments):
    facade = facade_for(monkeypatch, document())
    facade._indeterminate = True
    observed = []

    def read(**kwargs):
        observed.append(kwargs)
        return {"readback": True}

    monkeypatch.setattr(facade, tool, read)
    result = facade.invoke(tool, **arguments)

    assert result["success"] is True
    assert observed == [arguments]
    assert facade._indeterminate is True
    monkeypatch.setattr(facade, "layer_create", no_native_edit)
    assert facade.invoke("layer_create", name="Still blocked")["success"] is False


def test_validation_error_before_admission_does_not_latch_unknown_state(monkeypatch):
    facade = facade_for(monkeypatch, document())
    monkeypatch.setattr(facade, "_edit", no_native_edit)

    invalid = facade.invoke("document_resize", width=0, height=64)

    assert invalid["success"] is False
    assert invalid["_meta"]["photocraft"]["indeterminate"] is False
    admitted = []
    monkeypatch.setattr(facade, "layer_create", lambda **kwargs: admitted.append(kwargs) or {})
    assert facade.invoke("layer_create", name="Allowed")["success"] is True
    assert admitted == [{"name": "Allowed"}]


def test_budget_does_not_mutate_document_metadata():
    doc = document()
    original_bounds = list(doc["layers"][0]["bounds"])

    budget(doc, extra_pixels=MAX_PIXELS, extra_layers=1)
    resize_budget(doc, 128, 128)

    assert doc["layers"][0]["bounds"] == original_bounds
    assert (doc["width"], doc["height"]) == (64, 64)


def test_duplicate_fill_reserves_canvas_pixels_before_edit(monkeypatch):
    fills = [pixel(i, kind="Fill", bounds=None) for i in range(1, 5)]
    doc = document(*fills, width=4096, height=4096)
    facade = facade_for(monkeypatch, doc)
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke("layer_duplicate", layer=1)
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


@pytest.mark.parametrize("black,white", [(254, 255), (0, 1), (50, 51)])
def test_levels_rejects_values_native_would_silently_normalize(monkeypatch, black, white):
    facade = facade_for(monkeypatch, document())
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke("adjustment_tone", kind="levels", in_black=black, in_white=white)
    assert result["error"] == "invalid_arguments"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("selection_fill", {"layer": 1, "color": "#ffffff"}),
        ("selection_rect", {"x": 0, "y": 0, "width": 64, "height": 64, "mode": "add"}),
    ],
)
def test_union_of_distant_content_is_budgeted_before_edit(monkeypatch, tool, arguments):
    doc = document(
        pixel(1, bounds=[-8192, -8192, 1, 1]), hasSelection=True, selectionBounds=[0, 0, 64, 64]
    )
    if tool == "selection_rect":
        doc["selectionBounds"] = [-8192, -8192, 1, 1]
    facade = facade_for(monkeypatch, doc)
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke(tool, **arguments)
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


def test_text_extent_rejected_before_engine_even_when_area_fits(monkeypatch):
    facade = facade_for(monkeypatch, document())
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke("text_create", text="W" * 40, x=4096, y=0, size=128)
    assert result["error"] == "resource_limit"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


@pytest.mark.parametrize("kind", ["Smart Object", "Text", "Fill", "Group", "Adjustment"])
@pytest.mark.parametrize("tool", ["noise_reduce", "noise_despeckle"])
def test_noise_refuses_non_pixel_branches_before_dispatch(monkeypatch, kind, tool):
    facade = facade_for(monkeypatch, document(pixel(1, kind=kind)))
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke(tool, layer=1)
    assert result["error"] == "noise_pixel_only"
    assert result["_meta"]["photocraft"]["indeterminate"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"channels": {"alpha": [{}], "quickMask": None, "target": {"kind": "composite"}}},
        {"channels": {"alpha": [], "quickMask": None, "target": {"kind": "color", "index": 0}}},
        {"quickMask": True},
        {"activeLayer": 2},
        {"selectedLayers": [1, 2]},
        {"hasSelection": True, "selectionBounds": [-1, 0, 32, 32]},
    ],
)
def test_noise_refuses_ambiguous_targets_or_unbounded_selection(monkeypatch, change):
    facade = facade_for(monkeypatch, document(pixel(1), **change))
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke("noise_reduce", layer=1)
    assert result["success"] is False
    assert result["_meta"]["photocraft"]["indeterminate"] is False


def test_selection_from_mask_uses_only_current_document_mask(monkeypatch):
    facade = facade_for(monkeypatch, document(pixel(1, hasMask=True)))
    calls = []
    monkeypatch.setattr(
        facade, "_edit", lambda command, params: calls.append((command, params)) or {}
    )
    assert facade.invoke("selection_from_mask", layer=1, invert=True)["success"]
    assert calls == [
        (
            "select.loadSelection",
            {"channel": "mask", "layer": 1, "operation": "new", "invert": True},
        )
    ]


def test_selection_fill_inactive_pixel_is_rejected_before_active_layer_predicate(monkeypatch):
    doc = document(
        pixel(1),
        pixel(2, kind="Adjustment", bounds=None),
        activeLayer=2,
        selectedLayers=[2],
        hasSelection=True,
        selectionBounds=[0, 0, 8, 8],
    )
    facade = facade_for(monkeypatch, doc)
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    result = facade.invoke("selection_fill", layer=1, color="#ffffff")
    assert result["error"] == "single_active_layer_required"
    assert not facade._indeterminate


def test_undo_unavailable_is_a_preflight_error(monkeypatch):
    facade = facade_for(monkeypatch, document(canUndo=False))
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    assert facade.invoke("undo")["error"] == "undo_unavailable"
    assert not facade._indeterminate


def test_transform_empty_layer_is_rejected_before_native_mutation(monkeypatch):
    facade = facade_for(monkeypatch, document(pixel(1, bounds=[0, 0, 0, 0])))
    monkeypatch.setattr(facade, "_edit", no_native_edit)
    assert facade.invoke("layer_transform", layer=1)["error"] == "transform_empty_layer"
    assert not facade._indeterminate


@pytest.mark.parametrize("action", ["select", "close"])
def test_absent_document_index_never_sends_a_mutation(monkeypatch, action):
    facade = facade_for(monkeypatch, document())
    calls = []

    def read(name, args=None, **kwargs):
        calls.append((name, kwargs))
        assert name == "session_list" and not kwargs.get("mutating")
        return {"documents": [{"index": 0}]}

    monkeypatch.setattr(facade, "_json", read)
    assert (
        facade.invoke("document_session", action=action, index=3)["error"]
        == "document_index_unavailable"
    )
    assert not facade._indeterminate
