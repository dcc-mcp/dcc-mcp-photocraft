"""Bounded, fixed-command editing operations for the released headless engine."""

import json
import math
from importlib.resources import files

from .errors import AdapterError
from .paths import MAX_PIXELS
from .safety import (
    affine_bounds,
    bounds,
    budget,
    find_layer,
    geometry,
    layers,
    surface_pixels,
    union_bounds,
)


def number(value, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError("invalid_arguments")
    if (
        not math.isfinite(value)
        or not low <= value <= high
        or (integer and not isinstance(value, int))
    ):
        raise AdapterError("invalid_arguments")
    return value


def identity(value):
    return number(value, 0, 2**32 - 1, True)


def enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise AdapterError("invalid_arguments")
    return value


class EditingTools:
    TOOL_NAMES = (
        "capabilities_query",
        "layer_select",
        "layer_duplicate",
        "layer_arrange",
        "selection_rect",
        "selection_clear",
        "mask_paint",
        "mask_enabled",
        "image_crop",
        "canvas_resize",
        "layer_translate",
        "layer_transform",
        "layer_import",
    )

    @staticmethod
    def _verify(condition):
        if not condition:
            raise AdapterError("postcondition_failed", indeterminate=True)

    def capabilities_query(self, query="", category="", status="", offset=0, limit=25):
        for value in (query, category, status):
            if not isinstance(value, str) or len(value) > 120:
                raise AdapterError("invalid_arguments")
        enum(status, {"", "typed", "partial", "unexposed", "blocked", "main_only"})
        number(offset, 0, 10000, True)
        number(limit, 1, 100, True)
        data = json.loads(
            files("dcc_mcp_photocraft").joinpath("capabilities.json").read_text("utf-8")
        )
        entries = [
            entry
            for entry in data["entries"]
            if (not query or query.casefold() in (entry["id"] + " " + entry["label"]).casefold())
            and (not category or entry["category"] == category)
            and (not status or entry["exposure_status"] == status)
        ]
        if offset and offset >= len(entries):
            raise AdapterError("invalid_arguments")
        return {
            "upstream": data["upstream"],
            "total": len(entries),
            "offset": offset,
            "entries": entries[offset : offset + limit],
            "next_offset": offset + limit if offset + limit < len(entries) else None,
        }

    def layer_select(self, layer, mode="replace"):
        identity(layer)
        enum(mode, {"replace", "add", "toggle", "range"})
        find_layer(self._inspect(), layer)
        result = self._edit("layer.select", {"layer": layer, "mode": mode})
        if mode == "replace":
            self._verify(result["document"].get("selectedLayers") == [layer])
        return result

    def layer_duplicate(self, layer):
        identity(layer)
        doc = self._inspect()
        item, _ = find_layer(doc, layer)
        subtree = layers({"layers": [item]})
        budget(
            doc,
            extra_layers=len(subtree),
            extra_pixels=sum(
                surface_pixels(v)
                + (
                    doc["width"] * doc["height"]
                    if v.get("kind") == "Fill" and v.get("bounds") is None
                    else 0
                )
                for v, _ in subtree
            ),
        )
        before = {v["id"] for v, _ in layers(doc)}
        result = self._edit("layer.duplicate", {"layer": layer})
        new_id = result["command_result"].get("layer")
        self._verify(new_id not in before and new_id == result["document"].get("activeLayer"))
        self._verify(find_layer(result["document"], new_id)[0]["kind"] == item["kind"])
        result.update(layer=new_id, verified=True)
        return result

    def layer_arrange(self, layer, target, position="above"):
        identity(layer)
        identity(target)
        enum(position, {"above", "below"})
        doc = self._inspect()
        _, parent = find_layer(doc, layer)
        _, target_parent = find_layer(doc, target)
        if layer == target or parent != target_parent:
            raise AdapterError("arrange_same_parent_required")
        result = self._edit(
            "layer.moveTo", {"layer": layer, "target": target, "position": position}
        )
        siblings = [v["id"] for v, p in layers(result["document"]) if p == parent]
        self._verify(
            siblings.index(layer) == siblings.index(target) + (-1 if position == "above" else 1)
        )
        result["verified"] = True
        return result

    def selection_rect(self, x, y, width, height, mode="replace", ellipse=False, feather=0):
        for value in (x, y):
            number(value, 0, 4096, True)
        for value in (width, height):
            number(value, 1, 4096, True)
        enum(mode, {"replace", "add", "subtract", "intersect"})
        if not isinstance(ellipse, bool):
            raise AdapterError("invalid_arguments")
        number(feather, 0, 32)
        doc = self._inspect()
        if x + width > doc["width"] or y + height > doc["height"]:
            raise AdapterError("selection_outside_canvas")
        if mode == "add" and doc.get("hasSelection"):
            union_bounds(doc.get("selectionBounds"), [x, y, width, height])
        budget(
            doc,
            extra_pixels=(doc["width"] + 2 * math.ceil(feather * 3))
            * (doc["height"] + 2 * math.ceil(feather * 3)),
        )
        if (width + 2 * math.ceil(feather * 3)) * (
            height + 2 * math.ceil(feather * 3)
        ) > MAX_PIXELS:
            raise AdapterError("resource_limit")
        result = self._edit(
            "select.rect",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "mode": mode,
                "ellipse": ellipse,
                "feather": feather,
                "antiAlias": False,
            },
        )
        if mode == "replace" and not ellipse and not feather:
            self._verify(result["document"].get("selectionBounds") == [x, y, width, height])
            result["verified"] = True
        budget(result["document"])
        return result

    def selection_clear(self):
        doc = self._inspect()
        if not doc.get("hasSelection"):
            return {"document": doc, "verified": True}
        result = self._edit("select.deselect", {})
        self._verify(not result["document"].get("hasSelection"))
        result["verified"] = True
        return result

    def mask_enabled(self, layer, enabled):
        identity(layer)
        if not isinstance(enabled, bool):
            raise AdapterError("invalid_arguments")
        doc = self._inspect()
        item, _ = find_layer(doc, layer)
        if not item.get("hasMask"):
            raise AdapterError("mask_required")
        if doc.get("activeLayer") != layer or doc.get("selectedLayers") != [layer]:
            raise AdapterError("single_active_layer_required")
        # Native inspection exposes hasMask, not its enabled property. Pixel
        # readback / exported comparisons must verify the visible effect.
        return self._edit("layer.layerMask.enabled", {"layer": layer, "enabled": enabled})

    def mask_paint(self, layer, points, size=32, hardness=1, opacity=1, value="reveal"):
        identity(layer)
        number(size, 1, 256)
        number(hardness, 0, 1)
        number(opacity, 0, 1)
        enum(value, {"reveal", "hide"})
        if not isinstance(points, list) or not 1 <= len(points) <= 256:
            raise AdapterError("invalid_arguments")
        doc = self._inspect()
        budget(doc)
        item, _ = find_layer(doc, layer)
        if not item.get("hasMask"):
            raise AdapterError("mask_required")
        if doc.get("activeLayer") != layer or doc.get("selectedLayers") != [layer]:
            raise AdapterError("single_active_layer_required")
        converted = []
        for point in points:
            if not isinstance(point, dict) or set(point) - {"x", "y", "pressure"}:
                raise AdapterError("invalid_arguments")
            converted.append(
                [
                    number(point.get("x"), 0, doc["width"]),
                    number(point.get("y"), 0, doc["height"]),
                    number(point.get("pressure", 1), 0, 1),
                ]
            )
        length = sum(math.dist(a[:2], b[:2]) for a, b in zip(converted, converted[1:]))
        if (
            length > 32768
            or (length / max(1, size * 0.25) + len(points)) * size**2 > 4 * MAX_PIXELS
        ):
            raise AdapterError("resource_limit")
        if (doc["width"] + 2 * size) * (doc["height"] + 2 * size) > MAX_PIXELS:
            raise AdapterError("resource_limit")
        return self._edit(
            "paint.stroke",
            {
                "layer": layer,
                "points": converted,
                "size": size,
                "hardness": hardness,
                "opacity": opacity,
                "flow": 1,
                "spacing": 0.25,
                "smoothing": 0,
                "seed": 0,
                "target": "mask",
                "color": "#ffffff" if value == "reveal" else "#000000",
            },
        )

    def image_crop(self, x, y, width, height, delete_cropped_pixels=True):
        for value in (x, y):
            number(value, 0, 4096, True)
        for value in (width, height):
            number(value, 1, 4096, True)
        if not isinstance(delete_cropped_pixels, bool):
            raise AdapterError("invalid_arguments")
        doc = self._inspect()
        geometry(doc)
        if x + width > doc["width"] or y + height > doc["height"]:
            raise AdapterError("crop_outside_canvas")
        result = self._edit(
            "image.crop",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "deleteCroppedPixels": delete_cropped_pixels,
            },
        )
        self._verify(
            (result["document"].get("width"), result["document"].get("height")) == (width, height)
        )
        budget(result["document"])
        result["verified"] = True
        return result

    def canvas_resize(self, width, height, anchor="center", extension_color="transparent"):
        number(width, 1, 4096, True)
        number(height, 1, 4096, True)
        enum(
            anchor,
            {
                "topLeft",
                "top",
                "topRight",
                "left",
                "center",
                "right",
                "bottomLeft",
                "bottom",
                "bottomRight",
            },
        )
        if extension_color != "transparent":
            raise AdapterError("canvas_transparent_extension_only")
        doc = self._inspect()
        tree = geometry(doc)
        if width < doc["width"] or height < doc["height"]:
            raise AdapterError("canvas_expansion_only")
        ax = (
            0
            if anchor.endswith("Left") or anchor == "left"
            else 1
            if anchor.endswith("Right") or anchor == "right"
            else 0.5
        )
        ay = 0 if anchor.startswith("top") else 1 if anchor.startswith("bottom") else 0.5
        dx, dy = (
            math.floor((width - doc["width"]) * ax + 0.5),
            math.floor((height - doc["height"]) * ay + 0.5),
        )
        for item, _ in tree:
            x, y, w, h = bounds(item["bounds"])
            bounds([x + dx, y + dy, w, h])
        result = self._edit(
            "image.canvasSize",
            {"width": width, "height": height, "anchor": anchor, "extensionColor": "transparent"},
        )
        self._verify(
            (result["document"].get("width"), result["document"].get("height")) == (width, height)
        )
        self._verify(result["command_result"].get("offset") == [dx, dy])
        result["verified"] = True
        return result

    def layer_translate(self, layer, dx, dy):
        identity(layer)
        number(dx, -8192, 8192, True)
        number(dy, -8192, 8192, True)
        doc = self._inspect()
        item = geometry(doc, layer)[0][0]
        x, y, w, h = bounds(item["bounds"])
        expected = [x + dx, y + dy, w, h]
        bounds(expected)
        result = self._edit("layer.translate", {"layer": layer, "dx": dx, "dy": dy})
        if w and h:
            self._verify(find_layer(result["document"], layer)[0].get("bounds") == expected)
        result["verified"] = True
        return result

    def layer_transform(
        self,
        layer,
        scale_x=1,
        scale_y=None,
        translate_x=0,
        translate_y=0,
        rotation_degrees=0,
        interpolation="bicubic",
    ):
        identity(layer)
        number(scale_x, 0.01, 16)
        scale_y = scale_x if scale_y is None else number(scale_y, 0.01, 16)
        number(translate_x, -8192, 8192)
        number(translate_y, -8192, 8192)
        number(rotation_degrees, -180, 180)
        enum(interpolation, {"nearest", "bilinear", "bicubic"})
        doc = self._inspect()
        item = geometry(doc, layer)[0][0]
        angle = math.radians(rotation_degrees)
        if not all(bounds(item["bounds"])[2:]):
            raise AdapterError("transform_empty_layer")
        matrix = [
            scale_x * math.cos(angle),
            scale_x * math.sin(angle),
            -scale_y * math.sin(angle),
            scale_y * math.cos(angle),
            translate_x,
            translate_y,
        ]
        transformed = affine_bounds(item["bounds"], matrix)
        budget(doc, extra_pixels=math.prod(transformed[2:]))
        result = self._edit(
            "edit.transform", {"layer": layer, "matrix": matrix, "interpolation": interpolation}
        )
        budget(result["document"])
        result["transform"] = {
            "matrix": matrix,
            "origin": "document",
            "bounded_target": list(transformed),
        }
        return result

    def layer_import(self, path, name=None):
        if name is not None and (
            not isinstance(name, str) or not name or len(name) > 120 or "\x00" in name
        ):
            raise AdapterError("invalid_arguments")
        doc = self._layer_budget()
        if doc.get("hasSelection"):
            raise AdapterError("import_requires_no_selection")
        self._document_budget()
        state = self._json("session_list")
        target = state.get("active")
        if not isinstance(target, int) or not 0 <= target < len(state["documents"]):
            raise AdapterError("invalid_upstream_result")
        staged = self.workspace.stage_input(path)
        source_record = self.workspace.manifest()["inputs"][-1]
        budget(doc, extra_layers=1, extra_pixels=math.prod(source_record["dimensions"]))
        self._operation_stage = "open_source"
        self._json("doc_open", {"path": staged}, mutating=True)
        state_after = self._json("session_list")
        source = state_after.get("active")
        self._verify(
            source == len(state["documents"]) and len(state_after["documents"]) == source + 1
        )
        imported = self._inspect()
        geometry(imported)
        self._operation_stage = "copy_source"
        copied = self._json("command_run", {"id": "edit.copy", "params": {}}, mutating=True)
        x, y, w, h = bounds(copied.get("bounds"))
        budget(doc, extra_layers=1, extra_pixels=w * h)
        self._operation_stage = "select_target"
        self._json("doc_select", {"index": target}, mutating=True)
        current = self._inspect()
        self._verify(
            all(
                current.get(k) == doc.get(k)
                for k in ("name", "width", "height", "revision", "activeLayer")
            )
        )
        self._operation_stage = "paste_layer"
        pasted = self._json(
            "command_run",
            {"id": "edit.paste", "params": {"center": [x + w / 2, y + h / 2]}},
            mutating=True,
        )
        new_id = pasted.get("layer")
        current = self._inspect()
        item, _ = find_layer(current, new_id)
        self._verify(item.get("kind") == "Pixel" and item.get("bounds") == [x, y, w, h])
        if name is not None:
            self._operation_stage = "name_layer"
            self._json(
                "command_run",
                {"id": "layer.setProps", "params": {"layer": new_id, "name": name}},
                mutating=True,
            )
        self._operation_stage = "close_source"
        self._json("doc_close", {"index": source}, mutating=True)
        self._operation_stage = "purge_clipboard"
        self._json("command_run", {"id": "edit.purge.clipboard", "params": {}}, mutating=True)
        current = self._inspect()
        self._verify(
            current.get("activeLayer") == new_id and len(layers(current)) == len(layers(doc)) + 1
        )
        budget(current)
        return {
            "document": current,
            "layer": new_id,
            "readback": True,
            "verified": True,
            "source": self.workspace.manifest()["inputs"][-1],
            "placement": "original_pixel_coordinates",
            "undo_note": "Import is multiple native operations; it is not one atomic undo step.",
        }
