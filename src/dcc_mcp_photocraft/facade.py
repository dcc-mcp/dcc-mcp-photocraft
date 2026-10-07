"""Typed PhotoCraft v0.2.0 operations and their readback postconditions."""

import base64
import contextvars
import json
import math
import threading
from io import BytesIO

from dcc_mcp_core import current_job_id
from dcc_mcp_core.skills_helper import check_dcc_cancelled, skill_error, skill_success
from PIL import Image

from .editing import EditingTools
from .errors import AdapterError
from .paths import MAX_PIXELS
from .safety import MAX_LAYERS, MAX_TOTAL_PIXELS, bounds, budget, resize_budget
from .tonal import TonalTools

ACTIVE_FACADE = contextvars.ContextVar("photocraft_facade")
MAX_DOCUMENTS = 4


def _number(value, minimum, maximum, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError("invalid_arguments")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise AdapterError("invalid_arguments")
    if integer and not isinstance(value, int):
        raise AdapterError("invalid_arguments")
    return value


def _string(value, limit=120):
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise AdapterError("invalid_arguments")
    return value


def _color(value):
    import re

    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise AdapterError("invalid_arguments")
    return value


class PhotoCraftFacade(EditingTools, TonalTools):
    TOOL_NAMES = (
        (
            "connection_status",
            "document_session",
            "document_new",
            "document_open",
            "document_inspect",
            "document_save_as",
            "layer_create",
            "layer_set",
            "mask_set",
            "adjustment_create",
            "text_create",
            "document_resize",
            "preview",
            "export_image",
            "undo",
        )
        + EditingTools.TOOL_NAMES
        + TonalTools.TOOL_NAMES
    )

    def __init__(self, transport):
        self.transport = transport
        self.workspace = transport.workspace
        self._lock = threading.RLock()
        self._mutated = False
        self._indeterminate = False
        self._operation_stage = None

    def invoke(self, tool_name, **arguments):
        with self._lock:
            self._mutated = False
            self._operation_stage = None
            try:
                check_dcc_cancelled()
                if tool_name not in self.TOOL_NAMES:
                    raise AdapterError("unsupported_tool")
                diagnostic = tool_name in {
                    "connection_status",
                    "document_inspect",
                    "capabilities_query",
                } or (tool_name == "document_session" and arguments.get("action", "list") == "list")
                if self._indeterminate and not diagnostic:
                    raise AdapterError("session_requires_recovery", indeterminate=True)
                result = getattr(self, tool_name)(**arguments)
                return skill_success("PhotoCraft operation completed.", **result)
            except AdapterError as error:
                unknown = error.indeterminate or self._mutated
                self._indeterminate |= unknown
                return skill_error(
                    "PhotoCraft operation failed; inspect recorded artifacts before recovery.",
                    error.code,
                    _meta={
                        "photocraft": {
                            "indeterminate": unknown,
                            "retry_safe": False,
                            "stage": self._operation_stage,
                        }
                    },
                )
            except (TypeError, ValueError):
                self._indeterminate |= self._mutated
                return skill_error(
                    "Arguments or readback do not match the typed operation.",
                    "readback_failed" if self._mutated else "invalid_arguments",
                    _meta={"photocraft": {"indeterminate": self._mutated, "retry_safe": False}},
                )
            except Exception:
                if not self._mutated:
                    raise
                self._indeterminate = True
                return skill_error(
                    "An admitted operation could not finish its mandatory readback.",
                    "operation_failed_after_edit",
                    _meta={
                        "photocraft": {
                            "indeterminate": True,
                            "retry_safe": False,
                            "stage": self._operation_stage,
                        }
                    },
                )

    def _call(self, name, arguments, *, mutating=False):
        # Once an edit is admitted, finish mandatory readback even if a Core
        # cancellation arrives. Native PhotoCraft calls cannot be pre-empted.
        if mutating or not self._mutated:
            try:
                check_dcc_cancelled()
            except Exception:
                if self._mutated:
                    raise AdapterError("cancelled_after_partial_edit", indeterminate=True) from None
                raise
        if mutating:
            self._mutated = True
        job_id = current_job_id()
        meta = {"dcc-mcp/job_id": job_id} if job_id else None
        result = self.transport.call(name, arguments, mutating=mutating, meta=meta)
        if result.get("isError"):
            # Do not expose arbitrary upstream messages containing paths.
            raise AdapterError("upstream_tool_error", indeterminate=mutating)
        return result

    def _json(self, name, arguments=None, *, mutating=False):
        result = self._call(name, arguments or {}, mutating=mutating)
        try:
            blocks = [v["text"] for v in result["content"] if v.get("type") == "text"]
            if len(blocks) != 1:
                raise ValueError()
            return self._redact(json.loads(blocks[0]))
        except (ValueError, KeyError, TypeError):
            raise AdapterError("invalid_upstream_result", indeterminate=mutating) from None

    def _redact(self, value):
        """Project host paths to relative artifacts before Core can retain them."""
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in {"path", "filePath", "filename"} and isinstance(item, str):
                    from pathlib import Path

                    try:
                        item = (
                            Path(item).resolve().relative_to(self.workspace.output_root).as_posix()
                        )
                        if item.startswith(".imports/"):
                            item = "staged-input"
                    except ValueError:
                        item = "external-path"
                result[key] = self._redact(item)
            return result
        return value

    def _inspect(self):
        doc = self._json("doc_inspect")
        if not isinstance(doc, dict):
            raise AdapterError("invalid_upstream_result")
        return doc

    def _edit(self, command, params):
        native = self._json("command_run", {"id": command, "params": params}, mutating=True)
        doc = self._inspect()
        budget(doc)
        return {"document": doc, "command_result": native, "readback": True}

    def _document_budget(self):
        state = self._json("session_list")
        documents = state.get("documents", []) if isinstance(state, dict) else state
        if not isinstance(documents, list) or len(documents) >= MAX_DOCUMENTS:
            raise AdapterError("document_limit")

    def _layer_budget(self):
        doc = self._inspect()
        budget(doc, extra_layers=1)
        return doc

    def connection_status(self):
        return {
            **self.transport.status(),
            "manifest": self.workspace.manifest(),
            "requires_recovery": self._indeterminate,
            "tools": list(self.TOOL_NAMES),
            "limits": {
                "max_pixels": MAX_PIXELS,
                "max_total_pixels": MAX_TOTAL_PIXELS,
                "max_documents": MAX_DOCUMENTS,
                "max_layers": MAX_LAYERS,
            },
        }

    def document_session(self, action="list", index=None):
        if action == "list" and index is None:
            return {"session": self._json("session_list")}
        if action not in {"select", "close"}:
            raise AdapterError("invalid_arguments")
        _number(index, 0, MAX_DOCUMENTS - 1, integer=True)
        before = self._json("session_list")
        if index >= len(before.get("documents", [])):
            raise AdapterError("document_index_unavailable")
        self._json("doc_" + action, {"index": index}, mutating=True)
        return {"session": self._json("session_list"), "readback": True}

    def document_new(self, width, height, name="Untitled", background="#ffffff"):
        _number(width, 1, 4096, integer=True)
        _number(height, 1, 4096, integer=True)
        _string(name)
        if background != "transparent":
            _color(background)
        self._document_budget()
        self._json(
            "doc_new",
            {
                "width": width,
                "height": height,
                "mode": "rgb",
                "depth": 8,
                "name": name,
                "background": background,
            },
            mutating=True,
        )
        doc = self._inspect()
        if (doc.get("width"), doc.get("height")) != (width, height):
            raise AdapterError("postcondition_failed", indeterminate=True)
        return {"document": doc, "verified": True}

    def document_open(self, path, source="input"):
        self._document_budget()
        if source == "input":
            opened = self.workspace.stage_input(path)
        elif source == "output":
            self.workspace.reopen_file(path)
            opened = path
        else:
            raise AdapterError("invalid_arguments")
        opened_result = self._json("doc_open", {"path": opened}, mutating=True)
        doc = self._inspect()
        if doc.get("width", MAX_PIXELS + 1) * doc.get("height", MAX_PIXELS + 1) > MAX_PIXELS:
            # Native documents are not raster-decoded by Pillow. Refuse further
            # work and end this session if a native file exceeds our budget.
            self.transport.close()
            raise AdapterError("resource_limit", indeterminate=True)
        return {"document": doc, "warnings": opened_result.get("warnings", []), "readback": True}

    def document_inspect(self):
        return {"document": self._inspect()}

    def document_save_as(self, path):
        self.workspace.output_file(path, {".pcraft"})
        before = self._inspect()
        result = self._json("doc_save", {"path": path, "format": "pcraft"}, mutating=True)
        artifact = self.workspace.mark_created(path)
        return {
            "artifact": artifact,
            "document": before,
            "warnings": result.get("warnings", []),
            "native_reopen_required": True,
        }

    def layer_create(self, name):
        _string(name)
        self._layer_budget()
        return self._edit("layer.new.layer", {"name": name})

    def layer_set(
        self, layer, name=None, opacity=None, visible=None, fill=None, blend=None, clipped=None
    ):
        _number(layer, 0, 2**32 - 1, integer=True)
        from .safety import find_layer

        find_layer(self._inspect(), layer)
        props = {"layer": layer}
        if name is not None:
            props["name"] = _string(name)
        if opacity is not None:
            props["opacity"] = _number(opacity, 0, 1)
        if visible is not None:
            if not isinstance(visible, bool):
                raise AdapterError("invalid_arguments")
            props["visible"] = visible
        if fill is not None:
            props["fill"] = _number(fill, 0, 1)
        if blend is not None:
            from .editing import enum

            props["blend"] = enum(
                blend, {"normal", "multiply", "screen", "overlay", "darken", "lighten"}
            )
        if clipped is not None:
            if not isinstance(clipped, bool):
                raise AdapterError("invalid_arguments")
            props["clipped"] = clipped
        if len(props) == 1:
            raise AdapterError("invalid_arguments")
        return self._edit("layer.setProps", props)

    def mask_set(self, mode="reveal_all", layer=None):
        from .safety import find_layer

        commands = {
            "reveal_all": "revealAll",
            "hide_all": "hideAll",
            "remove": "delete",
            "reveal_selection": "revealSelection",
            "hide_selection": "hideSelection",
        }
        if mode not in commands:
            raise AdapterError("invalid_arguments")
        doc = self._inspect()
        layer = doc.get("activeLayer") if layer is None else layer
        _number(layer, 0, 2**32 - 1, integer=True)
        item, _ = find_layer(doc, layer)
        budget(doc, extra_pixels=MAX_PIXELS if not item.get("hasMask") and mode != "remove" else 0)
        if mode.endswith("selection") and not doc.get("hasSelection"):
            raise AdapterError("selection_required")
        result = self._edit("layer.layerMask." + commands[mode], {"layer": layer})
        self._verify(find_layer(result["document"], layer)[0].get("hasMask") == (mode != "remove"))
        result["verified"] = True
        return result

    def adjustment_create(self, kind, saturation=0, hue=0, vibrance=0):
        _number(saturation, -100, 100)
        _number(hue, -180, 180)
        _number(vibrance, -100, 100)
        if kind == "hue_saturation" and vibrance == 0:
            command, params = "hueSaturation", {"hue": hue, "saturation": saturation}
        elif kind == "vibrance" and hue == 0:
            command, params = "vibrance", {"vibrance": vibrance, "saturation": saturation}
        else:
            raise AdapterError("invalid_arguments")
        self._layer_budget()
        return self._edit("layer.newAdjustmentLayer." + command, params)

    def text_create(self, text, x, y, size=36, color="#ffffff"):
        _string(text, 256)
        _number(x, -4096, 4096)
        _number(y, -4096, 4096)
        _number(size, 1, 128)
        _color(color)
        doc = self._layer_budget()
        dpi = _number(doc.get("resolution", 72), 1, 1200)
        # Upstream allocates the text ink rectangle before canvas clipping.
        # Bound a deliberately conservative glyph rectangle, not just canvas.
        lines = text.expandtabs(8).splitlines() or [text]
        if (
            len(lines) > 8
            or max(map(len, lines)) * len(lines) * (size * dpi / 72 * 2) ** 2 > MAX_PIXELS
        ):
            raise AdapterError("resource_limit")
        budget(
            doc,
            extra_pixels=math.ceil(max(map(len, lines)) * len(lines) * (size * dpi / 72 * 2) ** 2),
        )
        glyph = size * dpi / 72 * 2
        left, top = math.floor(x - glyph), math.floor(y - glyph)
        bounds(
            [
                left,
                top,
                math.ceil((max(map(len, lines)) + 2) * glyph),
                math.ceil((len(lines) + 2) * glyph),
            ]
        )
        return self._edit(
            "type.create", {"text": text, "x": x, "y": y, "size": size, "color": color}
        )

    def document_resize(self, width, height):
        _number(width, 1, 4096, integer=True)
        _number(height, 1, 4096, integer=True)
        resize_budget(self._inspect(), width, height)
        result = self._edit("image.imageSize", {"width": width, "height": height})
        if (result["document"].get("width"), result["document"].get("height")) != (width, height):
            raise AdapterError("postcondition_failed", indeterminate=True)
        result["verified"] = True
        return result

    def preview(self, path, max_side=1024):
        _number(max_side, 1, 2048, integer=True)
        destination = self.workspace.output_file(path, {".png"})
        result = self._call("doc_render_preview", {"max_side": max_side})
        try:
            images = [v for v in result["content"] if v.get("type") == "image"]
            if len(images) != 1 or images[0].get("mimeType") != "image/png":
                raise ValueError()
            data = base64.b64decode(images[0]["data"], validate=True)
            with Image.open(BytesIO(data)) as image:
                dimensions = image.size
                if max(dimensions) > max_side:
                    raise ValueError()
                image.verify()
            with destination.open("xb") as stream:
                stream.write(data)
        except (ValueError, KeyError, OSError):
            raise AdapterError("invalid_preview") from None
        return {
            "artifact": self.workspace.mark_created(path),
            "dimensions": list(dimensions),
            "verified": True,
        }

    def export_image(self, path):
        destination = self.workspace.output_file(path, {".png", ".jpg", ".jpeg"})
        doc = self._inspect()
        result = self._json("doc_export", {"path": path}, mutating=True)
        artifact = self.workspace.mark_created(path)
        try:
            with Image.open(destination) as image:
                if image.size != (doc["width"], doc["height"]):
                    raise ValueError()
                image.verify()
        except (ValueError, OSError):
            raise AdapterError("artifact_invalid", indeterminate=True) from None
        return {"artifact": artifact, "warnings": result.get("warnings", []), "verified": True}

    def undo(self):
        if not self._inspect().get("canUndo"):
            raise AdapterError("undo_unavailable")
        return self._edit("edit.undo", {})
