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
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from dcc_mcp_photocraft.errors import AdapterError
from dcc_mcp_photocraft.facade import PhotoCraftFacade
from dcc_mcp_photocraft.paths import Workspace
from dcc_mcp_photocraft.transport import ManagedPhotoCraft


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
    fields = ("id", "name", "kind", "opacity", "visible", "hasMask", "text", "adjustment")
    return [{key: layer[key] for key in fields if key in layer} for layer in document["layers"]]


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
    original_signature = layer_signature(doc)

    doc = call("document_resize", width=192, height=128)["document"]
    check("resize_readback", (doc["width"], doc["height"]) == (192, 128))
    doc = call("undo")["document"]
    check("undo_restores_dimensions", (doc["width"], doc["height"]) == (384, 256))
    check("undo_preserves_editable_layers", layer_signature(doc) == original_signature)
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
    }
    transport = None
    workspace = None
    fixture = None
    before_hash = None
    try:
        inputs = root / "inputs"
        inputs.mkdir()
        fixture = inputs / "gradient.png"
        make_fixture(fixture)
        before_hash = digest(fixture)
        workspace = Workspace(inputs, root / "outputs")
        report["executable_sha256"] = digest(args.executable)
        transport = ManagedPhotoCraft(args.executable, workspace)
        transport.start()
        exercise(LiveRun(PhotoCraftFacade(transport), report), workspace)
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
        if fixture is not None and before_hash is not None:
            after_hash = digest(fixture)
            unchanged = before_hash == after_hash
            report["checks"].append(
                {
                    "name": "input_sha256_unchanged",
                    "passed": unchanged,
                    "path": "inputs/gradient.png",
                    "before": before_hash,
                    "after": after_hash,
                }
            )
            if not unchanged:
                report["status"] = "failed"
        if workspace is not None:
            (root / "manifest.json").write_text(
                json.dumps(workspace.manifest(), indent=2) + "\n", encoding="utf-8"
            )
        report["completed_utc"] = datetime.now(UTC).isoformat()
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
    raise SystemExit(main())
