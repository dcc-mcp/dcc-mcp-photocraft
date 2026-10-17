#!/usr/bin/env python3
"""Optional, genuine Core-over-HTTP acceptance against an installed PhotoCraft CLI.

Run with Python 3.10+ and the optional ``mcp`` and ``Pillow`` dependencies. This
script is deliberately outside the default, host-independent unit test suite::

    python scripts/native_showcase.py --cli /path/to/photocraft-cli \
        --output evidence/native-core-acceptance

The output directory must not already exist. Inputs are original synthetic
geometry unless --input-dir supplies original-landscape.png and
original-sun-field.png (960x640 and 256x256 respectively). No GUI, credentials,
public gateway, fake host, external uploads, or production monkeypatches are
used. All editing goes through ClientSession -> Core's real loopback listener
-> packaged fixed Skill -> the installed official CLI's native MCP server.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PREFIX = "photocraft_core__"
TOOLS = {
    "document_info",
    "list_photocraft_commands",
    "list_photocraft_methods",
    "ui_inspect",
    "ui_screenshot",
    "execute_photocraft_image_batch",
}
ORIGINALS = ("original-landscape.png", "original-sun-field.png")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_hashes() -> dict[str, str]:
    files = [ROOT / "pyproject.toml", Path(__file__).resolve()]
    files.extend(p for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}


def directory_hashes(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)): sha(p) for p in sorted(directory.rglob("*")) if p.is_file()}


class NativeObservation:
    """Audit actual spawn requests and observe actual direct-child process IDs.

    CPython's audit hook observes subprocess.Popen without replacing it. The
    Linux /proc observer independently records successful native processes.
    Neither mechanism modifies execution or creates a fake transport.
    """

    def __init__(self, binary: Path):
        self.binary = binary.resolve()
        self.label = "startup"
        self.spawns: list[dict[str, Any]] = []
        self.processes: dict[int, dict[str, Any]] = {}
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        sys.addaudithook(self._audit)

    def _audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event == "subprocess.Popen" and Path(os.fsdecode(args[0])).resolve() == self.binary:
            self.spawns.append({"label": self.label, "argv": [os.fsdecode(x) for x in args[1]]})

    def children(self) -> dict[int, list[str]]:
        found = {}
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                status = (path / "status").read_text()
                if f"PPid:\t{os.getpid()}\n" not in status or (path / "exe").resolve() != self.binary:
                    continue
                found[int(path.name)] = [os.fsdecode(x) for x in (path / "cmdline").read_bytes().split(b"\0") if x]
            except (OSError, ProcessLookupError):
                continue
        return found

    def _watch(self) -> None:
        while not self.done.is_set():
            for pid, argv in self.children().items():
                self.processes.setdefault(pid, {"pid": pid, "label": self.label, "argv": argv})
            self.done.wait(0.01)

    def start(self) -> None:
        if not Path("/proc/self/status").exists():
            raise RuntimeError("This process-cleanup acceptance currently requires Linux /proc")
        self.thread.start()

    def stop(self) -> None:
        self.done.set()
        self.thread.join(timeout=2)


class Acceptance:
    def __init__(self, args: argparse.Namespace):
        from PIL import Image

        self.Image = Image
        self.args = args
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.inputs = self.output / "inputs"
        self.artifacts = self.output / "artifacts"
        self.calls = self.output / "calls"
        for directory in (self.inputs, self.artifacts, self.calls):
            directory.mkdir()
        self.observation = NativeObservation(args.cli)
        self.report: dict[str, Any] = {
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "versions": {name: importlib.metadata.version(name) for name in ("dcc-mcp-core", "mcp", "Pillow")},
            "python": sys.version,
            "cli": {"path": str(args.cli.resolve()), "sha256_before": sha(args.cli)},
            "source_hashes_before": source_hashes(),
            "transport": "mcp.ClientSession + streamablehttp_client -> real Core loopback MCP -> native CLI MCP",
            "checks": {},
            "calls": [],
            "servers": [],
        }
        self.prepare_inputs()
        self.report["input_hashes_before"] = self.input_hashes()

    def check(self, name: str, condition: Any, detail: Any = None) -> None:
        self.report["checks"][name] = {"passed": bool(condition), "detail": detail}
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    def prepare_inputs(self) -> None:
        from PIL import ImageDraw

        if self.args.input_dir:
            for name in ORIGINALS:
                shutil.copyfile(self.args.input_dir / name, self.inputs / name)
        else:
            base = self.Image.new("RGB", (960, 640))
            draw = ImageDraw.Draw(base)
            for y in range(640):
                draw.line((0, y, 959, y), fill=(16 + y // 6, 34 + y // 5, 53 + y // 5))
            draw.polygon(((0, 640), (350, 290), (780, 640)), fill=(45, 80, 96))
            draw.polygon(((360, 640), (735, 385), (960, 520), (960, 640)), fill=(12, 66, 75))
            base.save(self.inputs / ORIGINALS[0])
            sun = self.Image.new("RGB", (256, 256), (242, 180, 92))
            draw = ImageDraw.Draw(sun)
            draw.ellipse((24, 24, 232, 232), fill=(249, 217, 144))
            draw.rectangle((0, 205, 255, 255), fill=(216, 91, 65))
            sun.save(self.inputs / ORIGINALS[1])
        self.check("input_geometry_dimensions", self.Image.open(self.inputs / ORIGINALS[0]).size == (960, 640))
        self.check("input_smart_dimensions", self.Image.open(self.inputs / ORIGINALS[1]).size == (256, 256))
        alpha = self.Image.new("RGBA", (96, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(alpha)
        draw.rectangle((8, 8, 45, 55), fill=(125, 85, 210, 128))
        draw.ellipse((44, 8, 87, 55), fill=(40, 190, 95, 255))
        alpha.save(self.inputs / "original-alpha.png")
        (self.inputs / "symlink.png").symlink_to(ORIGINALS[0])
        (self.output / "outside").mkdir()
        (self.artifacts / "output-link").symlink_to("../outside", target_is_directory=True)

    def input_hashes(self) -> dict[str, str]:
        result = {"staged/" + name: sha(self.inputs / name) for name in (*ORIGINALS, "original-alpha.png")}
        if self.args.input_dir:
            result.update({"external-original/" + name: sha(self.args.input_dir / name) for name in ORIGINALS})
        return result

    def rgba(self, path: Path) -> Any:
        with self.Image.open(path) as image:
            image.load()
            return image.convert("RGBA")

    def pixels_equal(self, first: Path, second: Path) -> bool:
        a, b = self.rgba(first), self.rgba(second)
        return a.size == b.size and a.tobytes() == b.tobytes()

    async def call(self, session: Any, label: str, name: str, arguments: Any = None, error: bool = False) -> Any:
        if name.startswith(PREFIX):
            name = self.public_names[name[len(PREFIX) :]]
        self.observation.label = label
        spawn_before = len(self.observation.spawns)
        started = time.monotonic()
        response = await session.call_tool(name, arguments or {})
        raw = response.model_dump(mode="json", exclude_none=True)
        dump(
            self.calls / f"{len(self.report['calls']):03d}-{label}.json",
            {"arguments": arguments or {}, "response": raw},
        )
        calls = self.observation.spawns[spawn_before:]
        self.report["calls"].append(
            {
                "label": label,
                "tool": name,
                "is_error": response.isError,
                "seconds": time.monotonic() - started,
                "native_spawn_count": len(calls),
                "native_spawn_argv": [item["argv"] for item in calls],
            }
        )
        self.check(
            label + "_error_contract", bool(response.isError) is error, raw if error != bool(response.isError) else None
        )
        self.check(label + "_native_process_closed", not self.observation.children())
        self.check(label + "_private_stages_removed", not list(self.artifacts.glob(".photocraft-batch-*")))
        if error:
            return raw
        value = raw.get("structuredContent")
        if value is None:
            content = raw.get("content", [])
            self.check(label + "_json_content", len(content) == 1 and content[0].get("type") == "text")
            value = json.loads(content[0]["text"])
        self.check(label + "_no_nested_false_success", not self.failed(value))
        if name.endswith("execute_photocraft_image_batch"):
            self.check(label + "_exactly_one_native_process", len(calls) == 1, calls)
            self.check(label + "_native_mcp_mode", calls[0]["argv"][1] == "mcp", calls)
        return value

    @staticmethod
    def failed(value: Any) -> bool:
        if isinstance(value, dict):
            return (
                value.get("ok") is False
                or value.get("success") is False
                or value.get("isError") is True
                or any(Acceptance.failed(v) for v in value.values())
            )
        if isinstance(value, list):
            return any(Acceptance.failed(v) for v in value)
        return False

    async def discover(self, session: Any, label: str) -> None:
        initialized = await session.initialize()
        self.check(label + "_core_server_identity", initialized.serverInfo.name == "dcc-mcp-photocraft")
        skills = await self.call(session, label + "-list-skills", "list_skills")
        self.check(label + "_skill_discovered", any(row["name"] == "photocraft-core" for row in skills["skills"]))
        loaded = await self.call(session, label + "-load-skill", "load_skill", {"skill_name": "photocraft-core"})
        expected = {PREFIX + name for name in TOOLS}
        self.check(
            label + "_six_fixed_wrappers", loaded["loaded"] and set(loaded["registered_tools"]) == expected, loaded
        )
        pages = []
        all_tools = []
        cursor = None
        while True:
            catalog = await session.list_tools(cursor=cursor)
            pages.append(catalog.model_dump(mode="json", exclude_none=True))
            all_tools.extend(catalog.tools)
            cursor = catalog.nextCursor
            if not cursor:
                break
        dump(self.calls / f"{label}-tools-list.json", pages)
        listed = {tool.name for tool in all_tools}
        self.check(label + "_six_mcp_tools_listed", TOOLS <= listed or expected <= listed, sorted(listed))
        self.public_names = {name: name if name in listed else PREFIX + name for name in TOOLS}
        typed = next(tool for tool in all_tools if tool.name == self.public_names["execute_photocraft_image_batch"])
        self.check(label + "_typed_schema", typed.inputSchema.get("additionalProperties") is False)

    @staticmethod
    def step(op: str, **params: Any) -> dict[str, Any]:
        return {"op": op, "document": "scene", **params}

    def workflow(self) -> list[dict[str, Any]]:
        s = self.step
        return [
            s("open_png", input="inputs/" + ORIGINALS[0], base_layer="base"),
            s("set_layer", layer="base", name="Original landscape, intact"),
            s("export_png", output="00-imported.png"),
            s("place_png", input="inputs/" + ORIGINALS[1], layer="sun", center=[738, 207], scale=100),
            s("set_layer", layer="sun", name="Embedded original sun", opacity=1, visible=True),
            s("export_png", output="01-unmasked.png"),
            s("mask_rectangle", layer="sun", x=610, y=79, width=256, height=256, ellipse=True),
            s("set_mask", layer="sun", density=100, feather=0.8),
            s("export_png", output="02-masked.png"),
            s("set_mask", layer="sun", density=35),
            s("export_png", output="03-density35.png"),
            s("set_mask", layer="sun", density=100),
            s("export_png", output="04-density100.png"),
            s("set_mask", layer="sun", enabled=False),
            s("export_png", output="05-mask-disabled.png"),
            s("set_mask", layer="sun", enabled=True),
            s("curves", layer="grade", points=[[0, 0], [128, 155], [255, 255]]),
            s("set_layer", layer="grade", name="Editable Curves lift"),
            s("export_png", output="06-graded.png"),
            s("set_layer", layer="grade", visible=False),
            s("export_png", output="07-grade-hidden.png"),
            s("set_layer", layer="grade", visible=True),
            s("inspect"),
            s("save_project", output="solar-geometry.pcraft"),
            s("close"),
            s("reopen_project", output="solar-geometry.pcraft"),
            s("inspect"),
            s("export_png", output="08-reopened.png"),
            s("set_layer", layer="grade", visible=False),
            s("export_png", output="09-reopened-ungraded.png"),
            s("set_mask", layer="sun", enabled=False),
            s("export_png", output="10-reopened-unmasked.png"),
            s("set_layer", layer="sun", visible=False),
            s("export_png", output="11-recovered-original.png"),
            s("set_layer", layer="sun", visible=True, opacity=0.4),
            s("export_png", output="12-reopened-opacity.png"),
            s("set_layer", layer="sun", opacity=1),
            s("set_mask", layer="sun", enabled=True),
            s("set_layer", layer="grade", visible=True),
            s("export_png", output="13-restored.png"),
            s("inspect"),
            s("close"),
        ]

    def verify_workflow(self, value: dict[str, Any]) -> None:
        receipt = value["result"]
        dump(self.output / "workflow-receipt.json", receipt)
        self.check(
            "workflow_all_steps_completed",
            receipt.get("completed_steps") == len(self.workflow()),
            receipt.get("completed_steps"),
        )
        self.check("workflow_receipt_ok", receipt.get("ok") is True)
        expected_outputs = {step["output"] for step in self.workflow() if step["op"] in {"save_project", "export_png"}}
        self.check("workflow_exact_output_inventory", {item["path"] for item in receipt["outputs"]} == expected_outputs)
        self.check("workflow_exact_artifact_inventory", set(directory_hashes(self.artifacts)) == expected_outputs)
        a = self.artifacts

        def same(first, second):
            return self.pixels_equal(a / first, a / second)

        self.check("import_pixel_exact", self.pixels_equal(self.inputs / ORIGINALS[0], a / "00-imported.png"))
        self.check("mask_changes_pixels", not same("01-unmasked.png", "02-masked.png"))
        self.check(
            "mask_hides_square_corner",
            self.rgba(a / "02-masked.png").getpixel((620, 90)) == self.rgba(a / "00-imported.png").getpixel((620, 90)),
        )
        self.check(
            "mask_preserves_original_center",
            self.rgba(a / "02-masked.png").getpixel((738, 207))
            == self.rgba(self.inputs / ORIGINALS[1]).getpixel((128, 128)),
        )
        for name, first, second, equal in (
            ("mask_density_changes_pixels", "02-masked.png", "03-density35.png", False),
            ("mask_density_restores_exactly", "02-masked.png", "04-density100.png", True),
            ("mask_disable_restores_exactly", "01-unmasked.png", "05-mask-disabled.png", True),
            ("curves_change_pixels", "02-masked.png", "06-graded.png", False),
            ("curves_hide_restores_exactly", "02-masked.png", "07-grade-hidden.png", True),
            ("native_reopen_exact_pixels", "06-graded.png", "08-reopened.png", True),
            ("reopened_curves_still_editable", "02-masked.png", "09-reopened-ungraded.png", True),
            ("reopened_mask_still_editable", "01-unmasked.png", "10-reopened-unmasked.png", True),
            ("reopened_opacity_changes_pixels", "01-unmasked.png", "12-reopened-opacity.png", False),
            ("reopened_edits_revert_exactly", "06-graded.png", "13-restored.png", True),
        ):
            self.check(name, same(first, second) is equal)
        self.check(
            "reopened_original_layer_intact",
            self.pixels_equal(self.inputs / ORIGINALS[0], a / "11-recovered-original.png"),
        )
        for path in sorted(a.glob("*.png")):
            image = self.rgba(path)
            self.check("dimensions_" + path.stem, image.size == (960, 640))
            self.check("opaque_alpha_" + path.stem, image.getchannel("A").getextrema() == (255, 255))
        with zipfile.ZipFile(a / "solar-geometry.pcraft") as archive:
            self.check("native_archive_crc", archive.testzip() is None)
            manifest = json.loads(archive.read("manifest.json"))
            dump(self.output / "native-manifest.json", manifest)
            layers = manifest["document"]["layers"]
            self.check(
                "native_distinct_editable_layers", len(layers) == 3 and len({layer["id"] for layer in layers}) == 3
            )
            self.check(
                "native_layer_kinds",
                [layer["content"]["kind"] for layer in layers] == ["raster", "smart", "adjustment"],
            )
            self.check("native_original_is_embedded", layers[1]["content"]["source"]["kind"] == "embedded")
            mask = layers[1]["mask"]
            self.check(
                "native_mask_is_editable",
                mask["enabled"] and mask["density"] == 1 and abs(mask["feather"] - 0.8) < 0.0001,
            )
            self.check("native_curves_is_adjustment", "Curves" in layers[2]["content"]["adjustment"])
            with self.Image.open(io.BytesIO(archive.read("composite/preview.png"))) as preview:
                self.check(
                    "native_preview_matches_export",
                    preview.convert("RGBA").tobytes() == self.rgba(a / "06-graded.png").tobytes(),
                )
        for published in receipt["outputs"]:
            path = a / published["path"]
            self.check("receipt_sha_" + path.stem, sha(path) == published["sha256"])
            self.check("receipt_bytes_" + path.stem, path.stat().st_size == published["bytes"])
            if path.suffix == ".png":
                pixel_hash = hashlib.sha256(self.rgba(path).tobytes()).hexdigest()
                self.check("receipt_pixels_" + path.stem, pixel_hash == published["pixels"]["rgba_sha256"])

    async def normal(self, session: Any) -> None:
        commands = await self.call(session, "fixed-commands", PREFIX + "list_photocraft_commands")
        self.check("native_live_commands_nonempty", bool(commands["commands"]))
        methods = await self.call(session, "fixed-methods", PREFIX + "list_photocraft_methods")
        self.check("native_live_methods_nonempty", bool(methods["methods"]))
        for name in ("ui_inspect", "ui_screenshot"):
            error = await self.call(session, "fixed-" + name, PREFIX + name, error=True)
            self.check(name + "_honest_gui_requirement", "desktop app" in json.dumps(error))
        result = await self.call(
            session, "native-workflow", PREFIX + "execute_photocraft_image_batch", {"steps": self.workflow()}
        )
        self.verify_workflow(result)
        info = await self.call(
            session, "fixed-document-info", PREFIX + "document_info", {"path": "artifacts/solar-geometry.pcraft"}
        )
        self.check("document_info_fixed_action", info["action"] == "document_info")
        state = info["result"]
        self.check("document_info_native_dimensions", (state["width"], state["height"]) == (960, 640))
        self.check(
            "document_info_native_layer_kinds",
            sorted(layer["kind"] for layer in state["layers"]) == ["Adjustment", "Pixel", "Smart Object"],
        )
        await self.safety_cases(session)

    async def safety_cases(self, session: Any) -> None:
        s = self.step
        open_step = s("open_png", input="inputs/" + ORIGINALS[0], base_layer="base")
        errors = {
            "schema-extra": {"steps": [open_step], "surprise": True},
            "schema-bool-number": {"steps": [open_step, s("set_layer", layer="base", opacity=True)]},
            "unknown-op": {"steps": [s("command_run", id="layer.setProps", params={})]},
            "unknown-layer-alias": {"steps": [open_step, s("set_layer", layer="missing", visible=False)]},
            "unknown-document-alias": {"steps": [{"op": "inspect", "document": "missing"}]},
            "path-traversal-read": {"steps": [s("open_png", input="../outside.png", base_layer="base")]},
            "absolute-path-read": {"steps": [s("open_png", input=str(self.inputs / ORIGINALS[0]), base_layer="base")]},
            "path-symlink-read": {"steps": [s("open_png", input="inputs/symlink.png", base_layer="base")]},
            "path-traversal-write": {"steps": [open_step, s("export_png", output="../escape.png")]},
            "path-symlink-write": {"steps": [open_step, s("export_png", output="output-link/escape.png")]},
            "output-overwrite": {"steps": [open_step, s("export_png", output="06-graded.png")]},
            "output-duplicate": {
                "steps": [open_step, s("export_png", output="duplicate.png"), s("export_png", output="duplicate.png")]
            },
            "foreign-native-open": {
                "steps": [s("open_png", input="artifacts/solar-geometry.pcraft", base_layer="base")]
            },
            "unsaved-native-reopen": {"steps": [s("reopen_project", output="solar-geometry.pcraft")]},
        }
        for label, arguments in errors.items():
            before = directory_hashes(self.artifacts)
            await self.call(
                session, "reject-" + label, PREFIX + "execute_photocraft_image_batch", arguments, error=True
            )
            self.check(label + "_no_published_mutation", before == directory_hashes(self.artifacts))
            await self.call(
                session,
                "recover-" + label,
                PREFIX + "execute_photocraft_image_batch",
                {"steps": [open_step, s("inspect"), s("close")]},
            )
        alpha = [
            s("open_png", input="inputs/original-alpha.png", base_layer="alpha"),
            s("export_png", output="alpha-roundtrip.png"),
            s("close"),
        ]
        await self.call(session, "alpha-roundtrip", PREFIX + "execute_photocraft_image_batch", {"steps": alpha})
        self.check(
            "alpha_transparency_pixel_exact",
            self.pixels_equal(self.inputs / "original-alpha.png", self.artifacts / "alpha-roundtrip.png"),
        )
        self.check(
            "alpha_fixture_has_transparency",
            self.rgba(self.inputs / "original-alpha.png").getchannel("A").getextrema() == (0, 255),
        )

    async def exercise(self, url: str, label: str) -> None:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        def loopback_client(**kwargs: Any) -> Any:
            return httpx.AsyncClient(trust_env=False, **kwargs)

        async with streamablehttp_client(
            url, timeout=180, sse_read_timeout=180, httpx_client_factory=loopback_client
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await self.discover(session, label)
                if label == "authorized":
                    await self.normal(session)
                elif label == "restored-authority":
                    steps = [
                        self.step("open_png", input="inputs/" + ORIGINALS[0], base_layer="base"),
                        self.step("export_png", output="authority-recovered.png"),
                        self.step("close"),
                    ]
                    await self.call(session, label, PREFIX + "execute_photocraft_image_batch", {"steps": steps})
                    self.check(
                        "authority_recovery_pixel_exact",
                        self.pixels_equal(self.inputs / ORIGINALS[0], self.artifacts / "authority-recovered.png"),
                    )
                else:
                    before = directory_hashes(self.artifacts)
                    steps = [self.step("open_png", input="inputs/" + ORIGINALS[0], base_layer="base")]
                    if label == "no-write-authority":
                        steps.append(self.step("export_png", output="forbidden.png"))
                    result = await self.call(
                        session, label, PREFIX + "execute_photocraft_image_batch", {"steps": steps}, error=True
                    )
                    self.check(
                        label + "_explained",
                        ("read" if label == "no-read-authority" else "write") in json.dumps(result).lower(),
                    )
                    self.check(label + "_no_published_mutation", before == directory_hashes(self.artifacts))
                    legacy = await self.call(
                        session,
                        label + "-document-info",
                        PREFIX + "document_info",
                        {"path": "artifacts/solar-geometry.pcraft"},
                        error=label == "no-read-authority",
                    )
                    if label == "no-write-authority":
                        self.check("read_only_document_info_needs_no_write_authority", legacy["result"]["width"] == 960)

    def server_run(self, label: str, read: str | None, write: str | None) -> None:
        from dcc_mcp_photocraft.server import PhotoCraftMcpServer

        registry = self.output / ("registry-" + label)
        server = PhotoCraftMcpServer(
            port=0,
            gateway_port=0,
            registry_dir=str(registry),
            enable_gateway_failover=False,
            enable_file_logging=False,
            enable_job_persistence=False,
            enable_checkpoint_persistence=False,
            enable_checkpoint_tools=False,
            enable_telemetry=False,
            cli_binary=str(self.args.cli.resolve()),
            read_root=read,
            write_root=write,
        )
        handle = None
        try:
            handle = server.start(install_atexit_hook=False)
            self.check(
                label + "_real_loopback_listener", handle.port > 0 and handle.mcp_url().startswith("http://127.0.0.1:")
            )
            self.check(label + "_idempotent_start", server.start(install_atexit_hook=False) is handle)
            self.report["servers"].append({"label": label, "url": handle.mcp_url(), "port": handle.port})
            asyncio.run(self.exercise(handle.mcp_url(), label))
        finally:
            server.stop()
            self.check(label + "_dispatcher_stopped", not server._host_driver.is_running)
            if handle:
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.check(label + "_listener_closed", probe.connect_ex(("127.0.0.1", handle.port)) != 0)
            service_file = registry / "services.json"
            services = json.loads(service_file.read_text()) if service_file.exists() else []
            self.check(label + "_registry_empty", not services, services)
            self.check(label + "_no_native_children", not self.observation.children())

    def run(self) -> None:
        names = (
            "PHOTOCRAFT_CLI",
            "PHOTOCRAFT_READ_ROOT",
            "PHOTOCRAFT_WRITE_ROOT",
            "PHOTOCRAFT_CONTROL_PORT",
            "PHOTOCRAFT_CONTROL_TOKEN",
            "PHOTOCRAFT_CONTROL_TOKEN_FILE",
            "HOME",
        )
        original_env = {name: os.environ.get(name) for name in names}
        self.observation.start()
        try:
            for name in names:
                os.environ.pop(name, None)
            isolated_home = self.output / "home"
            isolated_home.mkdir()
            os.environ["HOME"] = str(isolated_home)
            # Deliberately hostile environment proves constructor configuration
            # reaches each fixed Skill entrypoint rather than ambient settings.
            for name in ("PHOTOCRAFT_CLI", "PHOTOCRAFT_READ_ROOT", "PHOTOCRAFT_WRITE_ROOT"):
                os.environ[name] = str(self.output / "does-not-exist")
            self.server_run("authorized", str(self.output), str(self.artifacts))
            for name in ("PHOTOCRAFT_READ_ROOT", "PHOTOCRAFT_WRITE_ROOT"):
                os.environ.pop(name, None)
            self.server_run("no-read-authority", None, str(self.artifacts))
            self.server_run("no-write-authority", str(self.output), None)
            self.server_run("restored-authority", str(self.output), str(self.artifacts))
            self.report["all_passed"] = True
        except BaseException as exc:
            self.report["all_passed"] = False
            self.report["failure"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            raise
        finally:
            self.observation.stop()
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            self.report["checks"]["outside_destination_untouched"] = {
                "passed": not list((self.output / "outside").iterdir())
            }
            self.report["source_hashes_after"] = source_hashes()
            self.report["input_hashes_after"] = self.input_hashes()
            self.report["cli"]["sha256_after"] = sha(self.args.cli)
            self.report["native_spawns"] = self.observation.spawns
            self.report["observed_native_processes"] = list(self.observation.processes.values())
            self.report["artifacts"] = {
                str(p.relative_to(self.artifacts)): {"sha256": sha(p), "bytes": p.stat().st_size}
                for p in sorted(self.artifacts.rglob("*"))
                if p.is_file()
            }
            for name, equal in (
                (
                    "all_original_inputs_unchanged",
                    self.report["input_hashes_before"] == self.report["input_hashes_after"],
                ),
                (
                    "all_adapter_sources_unchanged_during_run",
                    self.report["source_hashes_before"] == self.report["source_hashes_after"],
                ),
                ("official_cli_unchanged", self.report["cli"]["sha256_before"] == self.report["cli"]["sha256_after"]),
                ("all_native_processes_closed", not self.observation.children()),
                (
                    "workflow_native_process_observed",
                    any(item["label"] == "native-workflow" for item in self.observation.processes.values()),
                ),
            ):
                self.report["checks"][name] = {"passed": equal}
                if not equal:
                    self.report["all_passed"] = False
            self.report["all_passed"] = self.report.get("all_passed", False) and all(
                check["passed"] for check in self.report["checks"].values()
            )
            self.report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            report_path = self.output / "report.json"
            dump(report_path, self.report)
            # This separate manifest authenticates report.json without creating
            # an impossible self-referential hash inside the report itself.
            hashes = directory_hashes(self.output)
            dump(self.output / "SHA256SUMS.json", hashes)
            print(
                json.dumps(
                    {
                        "all_passed": self.report["all_passed"],
                        "checks": len(self.report["checks"]),
                        "report": str(report_path),
                        "report_sha256": sha(report_path),
                    }
                )
            )
        if not self.report["all_passed"]:
            raise AssertionError("Acceptance recorded an integrity failure")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--expected-cli-sha256")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Optional acceptance requires Python 3.10+; default adapter/unit installation remains Python 3.9+")
    if not args.cli.is_file():
        parser.error("--cli must name an installed official photocraft-cli binary")
    if args.expected_cli_sha256 and sha(args.cli) != args.expected_cli_sha256:
        parser.error("Official CLI SHA-256 does not match the expected release checksum")
    Acceptance(args).run()


if __name__ == "__main__":
    main()
