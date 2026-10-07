"""Public tool contracts and wrapper routing; never start the native host."""

import inspect
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from dcc_mcp_photocraft.editing import EditingTools
from dcc_mcp_photocraft.facade import ACTIVE_FACADE, PhotoCraftFacade
from dcc_mcp_photocraft.tonal import TonalTools

SKILL = Path(__file__).parents[1] / "src/dcc_mcp_photocraft/skills/photocraft-document"
NEW_TOOLS = set(EditingTools.TOOL_NAMES + TonalTools.TOOL_NAMES)
READ_ONLY = {
    "connection_status",
    "document_inspect",
    "capabilities_query",
    "document_sample_pixel",
}

# These are user requests, not fabricated document state. Wrappers route them to
# an in-memory recorder, and schema validation does not require a live document.
REQUESTS = {
    "connection_status": {},
    "document_session": {},
    "document_new": {"width": 64, "height": 64},
    "document_open": {"path": "source.png"},
    "document_inspect": {},
    "document_save_as": {"path": "result.pcraft"},
    "layer_create": {"name": "Paint"},
    "layer_set": {"layer": 1, "opacity": 0.5},
    "mask_set": {},
    "adjustment_create": {"kind": "hue_saturation"},
    "text_create": {"text": "PhotoCraft", "x": 0, "y": 0},
    "document_resize": {"width": 128, "height": 128},
    "preview": {"path": "preview.png"},
    "export_image": {"path": "result.png"},
    "undo": {},
    "capabilities_query": {},
    "layer_select": {"layer": 1},
    "layer_duplicate": {"layer": 1},
    "layer_arrange": {"layer": 1, "target": 2},
    "selection_rect": {"x": 0, "y": 0, "width": 16, "height": 16},
    "selection_clear": {},
    "mask_paint": {"layer": 1, "points": [{"x": 4, "y": 4}]},
    "mask_enabled": {"layer": 1, "enabled": True},
    "image_crop": {"x": 0, "y": 0, "width": 16, "height": 16},
    "canvas_resize": {"width": 128, "height": 128},
    "layer_translate": {"layer": 1, "dx": 2, "dy": -2},
    "layer_transform": {"layer": 1},
    "layer_import": {"path": "overlay.png"},
    "adjustment_tone": {"kind": "brightness_contrast"},
    "fill_layer": {"color": "#123abc"},
    "layer_group": {"name": "Artwork"},
    "layer_delete": {"layer": 1},
    "selection_all": {},
    "selection_invert": {},
    "selection_fill": {"layer": 1, "color": "#123abc"},
    "document_sample_pixel": {"x": 0, "y": 0},
    "noise_reduce": {"layer": 1},
    "noise_despeckle": {"layer": 1},
    "selection_from_mask": {"layer": 1},
}


@pytest.fixture(scope="module")
def contracts():
    entries = json.loads((SKILL / "tools.yaml").read_text("utf-8"))["tools"]
    assert len(entries) == len({entry["name"] for entry in entries}), "Duplicate tool names"
    return {entry["name"]: entry for entry in entries}


def validator(contracts, tool):
    return Draft202012Validator(contracts[tool]["input_schema"])


def facade_signature(tool):
    signature = inspect.signature(getattr(PhotoCraftFacade, tool))
    return signature.replace(
        parameters=[p for p in signature.parameters.values() if p.name != "self"]
    )


def test_all_39_typed_tools_are_published_once(contracts):
    assert len(PhotoCraftFacade.TOOL_NAMES) == 39
    assert set(contracts) == set(PhotoCraftFacade.TOOL_NAMES) == set(REQUESTS)


@pytest.mark.parametrize("tool", PhotoCraftFacade.TOOL_NAMES)
def test_schema_matches_facade_and_preserves_execution_contract(contracts, tool):
    contract = contracts[tool]
    schema = contract["input_schema"]
    Draft202012Validator.check_schema(schema)
    parameters = facade_signature(tool).parameters

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(parameters)
    assert set(schema.get("required", [])) == {
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    for name, prop in schema["properties"].items():
        if "default" in prop:
            assert prop["default"] == parameters[name].default
            assert Draft202012Validator(prop).is_valid(prop["default"])

    assert contract["execution"] == (
        "sync" if tool in {"connection_status", "capabilities_query"} else "async"
    )
    assert contract["job_strategy"] == "monolithic"
    assert contract["affinity"] == "any"
    assert contract["enforce_thread_affinity"] is True
    assert contract["requires_in_process"] is True
    assert contract["annotations"]["read_only_hint"] is (tool in READ_ONLY)
    assert contract["annotations"]["open_world_hint"] is False


@pytest.mark.parametrize("tool", PhotoCraftFacade.TOOL_NAMES)
@pytest.mark.parametrize("explicit", [False, True], ids=["defaults", "explicit"])
def test_wrapper_routes_to_the_same_typed_operation_without_native_calls(contracts, tool, explicit):
    source = (SKILL / contracts[tool]["source_file"]).resolve()
    assert source.is_relative_to(SKILL.resolve())
    main = runpy.run_path(str(source))["main"]
    signature = inspect.signature(main)
    expected_signature = facade_signature(tool)
    if tool in NEW_TOOLS:
        assert set(signature.parameters) == set(expected_signature.parameters)
        for name, parameter in signature.parameters.items():
            assert parameter.kind not in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }
            assert parameter.default == expected_signature.parameters[name].default

    # Sentinels check every keyword reaches the facade unchanged. Separate
    # schema cases below validate realistic user requests and input boundaries.
    request = (
        {name: object() for name in expected_signature.parameters} if explicit else REQUESTS[tool]
    )
    calls = []
    returned = {"success": True, "marker": object()}

    def record(tool_name, **arguments):
        calls.append((tool_name, arguments))
        return returned

    token = ACTIVE_FACADE.set(SimpleNamespace(invoke=record))
    try:
        assert main(**request) is returned
    finally:
        ACTIVE_FACADE.reset(token)
    assert len(calls) == 1
    name, arguments = calls[0]
    assert name == tool
    actual = expected_signature.bind(**arguments)
    expected = expected_signature.bind(**request)
    actual.apply_defaults()
    expected.apply_defaults()
    assert actual.arguments == expected.arguments


@pytest.mark.parametrize("tool", PhotoCraftFacade.TOOL_NAMES)
def test_schema_accepts_a_request_and_rejects_unknown_arguments(contracts, tool):
    check = validator(contracts, tool)
    check.validate(REQUESTS[tool])
    assert not check.is_valid({**REQUESTS[tool], "native_command": "arbitrary.command"})


@pytest.mark.parametrize(
    "tool,field,values",
    [
        ("document_open", "source", ["input", "output"]),
        ("layer_select", "mode", ["replace", "add", "toggle", "range"]),
        ("layer_arrange", "position", ["above", "below"]),
        ("selection_rect", "mode", ["replace", "add", "subtract", "intersect"]),
        ("mask_paint", "value", ["reveal", "hide"]),
        (
            "canvas_resize",
            "anchor",
            [
                "topLeft",
                "top",
                "topRight",
                "left",
                "center",
                "right",
                "bottomLeft",
                "bottom",
                "bottomRight",
            ],
        ),
        ("canvas_resize", "extension_color", ["transparent"]),
        ("layer_transform", "interpolation", ["nearest", "bilinear", "bicubic"]),
        ("fill_layer", "style", ["linear", "radial", "angle", "reflected", "diamond"]),
        (
            "capabilities_query",
            "status",
            ["", "typed", "partial", "unexposed", "blocked", "main_only"],
        ),
    ],
)
def test_native_enum_domains_are_deliberately_bounded(contracts, tool, field, values):
    check = validator(contracts, tool)
    request = {**REQUESTS[tool]}
    if tool == "fill_layer":
        request["end_color"] = "#ffffff"
    for value in values:
        check.validate({**request, field: value})
    assert not check.is_valid({**request, field: "unsupported_native_value"})
    assert not check.is_valid({**request, field: True})


@pytest.mark.parametrize(
    "tool,field,low,high,integer,overrides",
    [
        ("layer_translate", "dx", -8192, 8192, True, {}),
        ("layer_transform", "scale_x", 0.01, 16, False, {}),
        ("layer_transform", "rotation_degrees", -180, 180, False, {}),
        ("selection_rect", "width", 1, 4096, True, {}),
        ("selection_rect", "feather", 0, 32, False, {}),
        ("mask_paint", "size", 1, 256, False, {}),
        ("mask_paint", "hardness", 0, 1, False, {}),
        ("capabilities_query", "offset", 0, 10000, True, {}),
        ("capabilities_query", "limit", 1, 100, True, {}),
        ("adjustment_tone", "brightness", -150, 150, False, {}),
        ("adjustment_tone", "contrast", -50, 100, False, {}),
        ("adjustment_tone", "exposure", -20, 20, False, {"kind": "exposure"}),
        ("adjustment_tone", "offset", -0.5, 0.5, False, {"kind": "exposure"}),
        ("adjustment_tone", "gamma", 0.01, 9.99, False, {"kind": "exposure"}),
        ("adjustment_tone", "out_black", 0, 255, True, {"kind": "levels"}),
        ("document_sample_pixel", "x", 0, 4095, True, {}),
    ],
)
def test_numeric_limits_match_the_bounded_source_operations(
    contracts, tool, field, low, high, integer, overrides
):
    check = validator(contracts, tool)
    request = {**REQUESTS[tool], **overrides}
    for value in (low, high):
        check.validate({**request, field: value})
    for value in (low - 0.01, high + 0.01, True, "1"):
        assert not check.is_valid({**request, field: value})
    if integer:
        assert not check.is_valid({**request, field: low + 0.5})


def test_mask_set_keeps_active_layer_default_and_exposes_selection_modes(contracts):
    check = validator(contracts, "mask_set")
    check.validate({})
    for mode in ("reveal_all", "hide_all", "remove", "reveal_selection", "hide_selection"):
        check.validate({"mode": mode})
        check.validate({"mode": mode, "layer": 0})
        check.validate({"mode": mode, "layer": 2**32 - 1})
    for request in (
        {"mode": "apply"},
        {"layer": -1},
        {"layer": 2**32},
        {"layer": True},
        {"layer": 1.5},
    ):
        assert not check.is_valid(request)


def test_layer_set_supports_fill_blend_and_clipping_with_typed_values(contracts):
    check = validator(contracts, "layer_set")
    for blend in ("normal", "multiply", "screen", "overlay", "darken", "lighten"):
        for fill in (0, 1):
            for clipped in (False, True):
                check.validate({"layer": 1, "fill": fill, "blend": blend, "clipped": clipped})
    for properties in (
        {"fill": -0.01},
        {"fill": 1.01},
        {"fill": True},
        {"blend": "arbitrary_native_mode"},
        {"clipped": 1},
        {"clipped": "true"},
    ):
        assert not check.is_valid({"layer": 1, **properties})


def test_mask_paint_point_schema_bounds_work_and_rejects_extra_native_controls(contracts):
    check = validator(contracts, "mask_paint")
    check.validate({"layer": 1, "points": [{"x": 0, "y": 4096, "pressure": 0}]})
    check.validate({"layer": 1, "points": [{"x": 1, "y": 1, "pressure": 1}] * 256})
    for points in (
        [],
        [{"x": 1, "y": 1}] * 257,
        [{"x": 1}],
        [{"x": -1, "y": 1}],
        [{"x": 4097, "y": 1}],
        [{"x": True, "y": 1}],
        [{"x": 1, "y": 1, "pressure": 1.01}],
        [{"x": 1, "y": 1, "target": "pixels"}],
        [[1, 1, 1]],
    ):
        assert not check.is_valid({"layer": 1, "points": points})


def test_tone_curves_are_small_integer_pairs(contracts):
    check = validator(contracts, "adjustment_tone")
    check.validate({"kind": "curves", "points": [[0, 0], [255, 255]]})
    check.validate({"kind": "curves", "points": [[i, i] for i in range(19)]})
    for points in (
        [],
        [[0, 0]],
        [[i, i] for i in range(20)],
        [[0], [255, 255]],
        [[0, 0, 0], [255, 255]],
        [[-1, 0], [255, 255]],
        [[0, 0], [255, 256]],
        [[0, True], [255, 255]],
        [[0, 0.5], [255, 255]],
    ):
        assert not check.is_valid({"kind": "curves", "points": points})
    assert not check.is_valid({"kind": "native_script"})
