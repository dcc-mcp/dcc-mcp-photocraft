"""Exercise the real pinned PhotoCraft process through the adapter facade.

Install the adapter first (or set PYTHONPATH=src outside this script), then run::

    python scripts/headless_e2e.py --executable /path/to/photocraft-cli \
        --workspace /path/to/a-new-e2e-directory

This is a live headless engine test, not a gateway or desktop/GPU test. It
requires the official executable and never falls back to a mock or skips an
unavailable dependency. The workspace must not exist; artifacts are retained
for review. Report and manifest contain relative names and content hashes only.
"""

import argparse
import hashlib
import json
import platform
import random
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from dcc_mcp_photocraft.errors import AdapterError
from dcc_mcp_photocraft.facade import PhotoCraftFacade
from dcc_mcp_photocraft.paths import Workspace
from dcc_mcp_photocraft.transport import ManagedPhotoCraft

# These expectations are authored independently of the advertised tool list.
# Adding a registered tool without an observable scenario must fail coverage.
TOOL_POSTCONDITIONS = {
    "connection_status": "real_host_connected",
    "document_session": "final_session_empty",
    "document_new": "new_document_dimensions",
    "document_open": "import_dimensions",
    "document_inspect": "native_roundtrip_layer_state",
    "document_save_as": "native_roundtrip_pixels",
    "layer_create": "layer_properties_readback",
    "layer_set": "layer_properties_readback",
    "mask_set": "hidden_adjustment_restores_baseline_pixels",
    "adjustment_create": "adjustment_changes_pixels",
    "text_create": "text_changes_rendered_pixels",
    "document_resize": "resize_readback",
    "preview": "preview_decoded_independently",
    "export_image": "native_roundtrip_pixels",
    "undo": "undo_preserves_editable_layers",
    "capabilities_query": "capability_catalog_query",
    "layer_select": "selected_layer_readback",
    "layer_duplicate": "duplicate_is_independent_pixel_layer",
    "layer_arrange": "reorder_below_opaque_base_changes_pixels",
    "selection_rect": "selection_rectangle_readback",
    "selection_clear": "selection_cleared",
    "mask_paint": "mask_paint_reveals_only_local_pixels",
    "mask_enabled": "mask_disable_restores_original_pixels",
    "image_crop": "crop_is_exact_pixel_slice",
    "canvas_resize": "canvas_expansion_preserves_pixels_and_alpha",
    "layer_translate": "translation_exact_pixels",
    "layer_transform": "transform_scale_exact_pixels",
    "layer_import": "import_composite_exact_pixels",
    "adjustment_tone": "all_tone_kinds_have_visible_effect",
    "fill_layer": "solid_fill_exact_pixels",
    "layer_group": "group_is_editable_and_nonflattening",
    "layer_delete": "deleting_group_restores_prior_pixels",
    "selection_all": "selection_all_canvas_readback",
    "selection_invert": "inverted_selection_fill_exact_pixels",
    "selection_fill": "selection_fill_exact_pixels",
    "document_sample_pixel": "native_sample_agrees_with_export",
    "noise_reduce": "noise_reduce_lowers_noise_and_retains_markers",
    "noise_despeckle": "despeckle_reduces_noise_in_untreated_region",
    "selection_from_mask": "painted_mask_selection_limits_noise",
}


class E2EFailure(Exception):
    """A stable, public-safe failure code rather than raw host diagnostics."""


def require(condition, code):
    if not condition:
        raise E2EFailure(code)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(path):
    """Create original synthetic test data; no external image or private file."""
    width, height = 384, 256
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            (
                32 + (x * 159 // (width - 1)),
                48 + (y * 143 // (height - 1)),
                64 + ((x + y) * 111 // (width + height - 2)),
            )
            for y in range(height)
            for x in range(width)
        ]
    )
    image.save(path, format="PNG")


def make_fixtures(directory):
    make_fixture(directory / "gradient.png")
    # Binary alpha avoids making the oracle depend on the engine's blending
    # color space. Four opaque corners retain the full content bounds.
    plan = Image.new("RGBA", (96, 64))
    colors = ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255))
    plan.putdata(
        [
            (0, 0, 0, 0) if 40 <= x < 56 and 24 <= y < 40 else colors[(y >= 32) * 2 + (x >= 48)]
            for y in range(64)
            for x in range(96)
        ]
    )
    plan.save(directory / "plan.png")
    tones = Image.new("RGB", (50, 16))
    tones.putdata(
        [
            (value, value, value)
            for _ in range(16)
            for value in (0, 64, 128, 192, 255)
            for _ in range(10)
        ]
    )
    tones.save(directory / "tones.png")
    rng = random.Random(20261007)
    noise = Image.new("RGB", (128, 96))
    noise.putdata(
        [(value, value, value) for value in (128 + rng.randint(-24, 24) for _ in range(128 * 96))]
    )
    noise.paste((255, 255, 255), (8, 8, 24, 24))
    noise.paste((0, 0, 0), (104, 72, 120, 88))
    noise.save(directory / "noise.png")
    return {p.name: digest(p) for p in sorted(directory.iterdir()) if p.is_file()}


def source_manifest():
    root = Path(__file__).resolve().parent.parent
    paths = sorted(
        path
        for base in (root / "src", root / "scripts")
        for path in base.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".md"}
    )
    hashes = {p.relative_to(root).as_posix(): digest(p) for p in paths}
    normalized = {
        p.relative_to(root).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in paths
    }
    return {
        "files": hashes,
        "sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "lf_normalized_files": normalized,
        "lf_normalized_sha256": hashlib.sha256(
            json.dumps(normalized, sort_keys=True).encode()
        ).hexdigest(),
    }


def verify_coverage(run, advertised):
    calls = {step["tool"] for step in run.report["steps"] if step["status"] == "passed"}
    checks = {check["name"] for check in run.report["checks"] if check["passed"]}
    run.check(
        "advertised_tools_match_authored_scenarios", set(advertised) == set(TOOL_POSTCONDITIONS)
    )
    run.check(
        "every_tool_has_successful_call_and_postcondition",
        all(
            tool in calls and postcondition in checks
            for tool, postcondition in TOOL_POSTCONDITIONS.items()
        ),
        tool_postconditions=TOOL_POSTCONDITIONS,
    )


def load_rgba(path, expected_size):
    with Image.open(path) as image:
        image.load()
        require(image.format == "PNG", "independent_png_format")
        require(image.size == expected_size, "independent_png_dimensions")
        return image.convert("RGBA")


def pixels_equal(actual, expected):
    # Image.getbbox() alone on RGBA differences can miss RGB-only changes.
    return actual.size == expected.size and actual.tobytes() == expected.tobytes()


def composite(base, overlay, position):
    expected = base.convert("RGBA").copy()
    expected.alpha_composite(overlay, position)
    return expected


def load_rgb(path, expected_size):
    with Image.open(path) as image:
        image.load()
        require(image.format == "PNG", "independent_png_format")
        require(image.size == expected_size, "independent_png_dimensions")
        rgb = image.convert("RGB")
    require(any(low != high for low, high in rgb.getextrema()), "nonuniform_pixels")
    return rgb


def layer_signature(document):
    """The persistence contract excludes transient history and selection state."""
    fields = (
        "id",
        "name",
        "kind",
        "opacity",
        "visible",
        "hasMask",
        "text",
        "adjustment",
        "bounds",
        "blend",
        "fill",
        "clipped",
        "linkGroup",
    )
    result = []
    for layer in document["layers"]:
        item = {key: layer[key] for key in fields if key in layer}
        if "children" in layer:
            item["children"] = layer_signature({"layers": layer["children"]})
        result.append(item)
    return result


def public_document(document):
    return {
        "width": document["width"],
        "height": document["height"],
        "mode": document["mode"],
        "depth": document["depth"],
        "layers": layer_signature(document),
    }


class LiveRun:
    def __init__(self, facade, report):
        self.facade = facade
        self.report = report

    def call(self, tool, **arguments):
        step = {"tool": tool, "arguments": arguments, "status": "running"}
        self.report["steps"].append(step)
        result = self.facade.invoke(tool, **arguments)
        if result.get("success") is not True:
            step["status"] = "failed"
            # Core errors are machine-readable adapter-owned codes.
            code = result.get("error")
            step["error"] = code if isinstance(code, str) and code.isidentifier() else "tool_failed"
            raise E2EFailure("tool_failed_" + tool)
        step["status"] = "passed"
        return result["context"]

    def check(self, name, condition, **evidence):
        self.report["checks"].append({"name": name, "passed": bool(condition), **evidence})
        require(condition, name)

    def reject(self, tool, **arguments):
        result = self.facade.invoke(tool, **arguments)
        meta = result.get("_meta", {}).get("photocraft", {})
        require(result.get("success") is False, "expected_rejection_" + tool)
        require(meta.get("indeterminate") is False, "rejection_mutated_" + tool)
        self.report["steps"].append(
            {
                "tool": tool,
                "arguments": arguments,
                "status": "expected_rejection",
                "error": result.get("error"),
            }
        )
        return result


def exercise(run, workspace):
    call, check = run.call, run.check
    state = call("connection_status")
    check("real_host_connected", state["state"] == "connected", host=state)
    state = call("document_session", action="list")["session"]
    check("initial_session_empty", state["documents"] == [])

    doc = call("document_new", width=160, height=96, name="E2E scratch", background="#203040")[
        "document"
    ]
    check("new_document_dimensions", (doc["width"], doc["height"]) == (160, 96))
    state = call("document_session", action="select", index=0)["session"]
    check("document_select_readback", state["active"] == 0)
    state = call("document_session", action="close", index=0)["session"]
    check("new_document_closed", state["documents"] == [])

    doc = call("document_open", path="gradient.png", source="input")["document"]
    check("import_dimensions", (doc["width"], doc["height"]) == (384, 256))
    check("import_is_pixel_layer", len(doc["layers"]) == 1 and doc["layers"][0]["kind"] == "Pixel")
    source_layer = doc["layers"][0]["id"]
    call("export_image", path="baseline.png")
    baseline = load_rgb(workspace.output_root / "baseline.png", (384, 256))

    # Resize is intentionally tested before masks or type are introduced:
    # unknown native mask/type allocation bounds are rejected by the facade.
    original_signature = layer_signature(doc)
    doc = call("document_resize", width=192, height=128)["document"]
    check("resize_readback", (doc["width"], doc["height"]) == (192, 128))
    doc = call("undo")["document"]
    check("undo_restores_dimensions", (doc["width"], doc["height"]) == (384, 256))
    check("undo_preserves_editable_layers", layer_signature(doc) == original_signature)

    doc = call("adjustment_create", kind="vibrance", vibrance=40, saturation=15)["document"]
    check(
        "editable_adjustment",
        doc["layers"][0]["kind"] == "Adjustment" and "adjustment" in doc["layers"][0],
    )
    check(
        "source_layer_preserved",
        doc["layers"][-1]["id"] == source_layer and doc["layers"][-1]["kind"] == "Pixel",
    )
    doc = call("mask_set", mode="hide_all")["document"]
    check("hide_mask_exists", doc["layers"][0]["hasMask"] is True)
    call("export_image", path="masked.png")
    masked = load_rgb(workspace.output_root / "masked.png", (384, 256))
    check(
        "hidden_adjustment_restores_baseline_pixels",
        ImageChops.difference(baseline, masked).getbbox() is None,
    )
    doc = call("mask_set", mode="reveal_all")["document"]
    check("reveal_mask_exists", doc["layers"][0]["hasMask"] is True)
    call("export_image", path="graded.png")
    graded = load_rgb(workspace.output_root / "graded.png", (384, 256))
    difference = ImageChops.difference(baseline, graded)
    check(
        "adjustment_changes_pixels",
        difference.getbbox() is not None,
        mean_absolute_rgb_difference=ImageStat.Stat(difference).mean,
    )
    doc = call("mask_set", mode="remove")["document"]
    check("mask_removed", doc["layers"][0]["hasMask"] is False)
    call("mask_set", mode="reveal_all")

    doc = call("text_create", text="PhotoCraft E2E", x=24, y=76, size=32, color="#ffffff")[
        "document"
    ]
    check(
        "editable_text",
        doc["layers"][0]["kind"] == "Type" and doc["layers"][0]["text"]["text"] == "PhotoCraft E2E",
    )
    doc = call("layer_create", name="QA overlay")["document"]
    overlay = doc["layers"][0]["id"]
    doc = call("layer_set", layer=overlay, name="QA hidden overlay", opacity=0.5, visible=False)[
        "document"
    ]
    changed = next(layer for layer in doc["layers"] if layer["id"] == overlay)
    check(
        "layer_properties_readback",
        changed["name"] == "QA hidden overlay"
        and changed["opacity"] == 0.5
        and changed["visible"] is False,
    )
    layered_signature = layer_signature(doc)
    call("layer_set", layer=overlay, opacity=0.25)
    doc = call("undo")["document"]
    check("undo_preserves_layered_document", layer_signature(doc) == layered_signature)
    doc = call("document_inspect")["document"]
    before = public_document(doc)

    call("preview", path="preview.png", max_side=192)
    preview = load_rgb(workspace.output_root / "preview.png", (192, 128))
    check("preview_decoded_independently", preview.size == (192, 128))
    call("export_image", path="edited.png")
    edited = load_rgb(workspace.output_root / "edited.png", (384, 256))
    check(
        "text_changes_rendered_pixels", ImageChops.difference(graded, edited).getbbox() is not None
    )
    call("document_save_as", path="edited.pcraft")
    native_digest = digest(workspace.output_root / "edited.pcraft")
    state = call("document_session", action="close", index=0)["session"]
    check("native_closed_before_reopen", state["documents"] == [])
    doc = call("document_open", path="edited.pcraft", source="output")["document"]
    check(
        "native_roundtrip_layer_state",
        public_document(doc) == before,
        document=public_document(doc),
    )
    call("export_image", path="reopened.png")
    reopened = load_rgb(workspace.output_root / "reopened.png", (384, 256))
    check("native_roundtrip_pixels", ImageChops.difference(edited, reopened).getbbox() is None)
    check(
        "native_reopen_does_not_modify_file",
        digest(workspace.output_root / "edited.pcraft") == native_digest,
    )
    state = call("document_session", action="close", index=0)["session"]
    check("final_session_empty", state["documents"] == [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve()
    # Never delete or overwrite previous evidence, including failed runs.
    try:
        root.mkdir(parents=True, exist_ok=False)
    except OSError:
        print(json.dumps({"status": "failed", "error": "workspace_must_be_new_and_writable"}))
        return 1
    report = {
        "schema_version": 1,
        "test": "real_photocraft_headless_facade_e2e",
        "scope": "official executable -> MCP stdio -> typed adapter facade; no gateway or GUI",
        "started_utc": datetime.now(UTC).isoformat(),
        "status": "running",
        "versions": {
            "python": platform.python_version(),
            "dcc_mcp_core": version("dcc-mcp-core"),
            "mcp": version("mcp"),
            "Pillow": version("Pillow"),
        },
        "steps": [],
        "checks": [],
        "source": source_manifest(),
    }
    transport = None
    workspace = None
    fixture_hashes = {}
    try:
        inputs = root / "inputs"
        inputs.mkdir()
        fixture_hashes = make_fixtures(inputs)
        workspace = Workspace(inputs, root / "outputs")
        report["executable_sha256"] = digest(args.executable)
        transport = ManagedPhotoCraft(args.executable, workspace)
        transport.start()
        run = LiveRun(PhotoCraftFacade(transport), report)
        exercise(run, workspace)
        from extension_cases import exercise_extensions

        exercise_extensions(run, workspace)
        verify_coverage(run, run.call("connection_status")["tools"])
        report["status"] = "passed"
    except (E2EFailure, AdapterError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
    except Exception as error:  # noqa: BLE001 - redact unexpected host diagnostics at the CLI boundary.
        report["status"] = "failed"
        report["error"] = "unexpected_" + type(error).__name__
    finally:
        if transport is not None:
            try:
                transport.close()
                report["shutdown"] = transport.status()
                require(report["shutdown"]["state"] == "closed", "clean_shutdown")
            except (AdapterError, E2EFailure) as error:
                report["status"] = "failed"
                report["shutdown_error"] = str(error)
        if fixture_hashes:
            after_hashes = {name: digest(root / "inputs" / name) for name in fixture_hashes}
            unchanged = fixture_hashes == after_hashes
            report["checks"].append(
                {
                    "name": "input_sha256_unchanged",
                    "passed": unchanged,
                    "before": fixture_hashes,
                    "after": after_hashes,
                }
            )
            if not unchanged:
                report["status"] = "failed"
        if workspace is not None:
            (root / "manifest.json").write_text(
                json.dumps(workspace.manifest(), indent=2) + "\n", encoding="utf-8"
            )
        report["completed_utc"] = datetime.now(UTC).isoformat()
        if source_manifest()["sha256"] != report["source"]["sha256"]:
            report["status"] = "failed"
            report["source_changed_during_run"] = True
            report.setdefault("error", "source_changed_during_run")
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": "report.json",
                "manifest": "manifest.json",
                "calls": len(report["steps"]),
                "checks": len(report["checks"]),
                **({"error": report["error"]} if "error" in report else {}),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    # Shared extension scenarios import this module. Keep the exception type
    # identical when this file is executed as a script rather than imported.
    sys.modules["headless_e2e"] = sys.modules[__name__]
    raise SystemExit(main())
