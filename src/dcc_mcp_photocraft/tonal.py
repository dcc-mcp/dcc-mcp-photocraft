"""Typed tonal layers, fills and small document queries."""

from .editing import enum, identity, number
from .errors import AdapterError
from .safety import bounds, budget, find_layer, layers, union_bounds


def _hex_color(value):
    import re

    if not isinstance(value, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", value):
        raise AdapterError("invalid_arguments")
    return value


def _name(value):
    if not isinstance(value, str) or not value or len(value) > 120 or "\x00" in value:
        raise AdapterError("invalid_arguments")
    return value


class TonalTools:
    TOOL_NAMES = (
        "adjustment_tone",
        "fill_layer",
        "layer_group",
        "layer_delete",
        "selection_all",
        "selection_invert",
        "selection_fill",
        "document_sample_pixel",
        "noise_reduce",
        "noise_despeckle",
        "selection_from_mask",
    )

    def adjustment_tone(
        self,
        kind,
        brightness=0,
        contrast=0,
        exposure=0,
        offset=0,
        gamma=1,
        in_black=0,
        in_white=255,
        out_black=0,
        out_white=255,
        points=None,
    ):
        enum(kind, {"brightness_contrast", "exposure", "levels", "curves"})
        number(brightness, -150, 150)
        number(contrast, -50, 100)
        number(exposure, -20, 20)
        number(offset, -0.5, 0.5)
        number(gamma, 0.01, 9.99)
        for v in (in_black, in_white, out_black, out_white):
            number(v, 0, 255, True)
        if in_black > 253 or in_white < 2 or in_white - in_black < 2 or out_black > out_white:
            raise AdapterError("invalid_arguments")
        tonal_defaults = (in_black, in_white, out_black, out_white) == (0, 255, 0, 255)
        if (
            kind == "brightness_contrast"
            and exposure == offset == 0
            and gamma == 1
            and tonal_defaults
            and points is None
        ):
            command, params = (
                "brightnessContrast",
                {"brightness": brightness, "contrast": contrast, "legacy": False},
            )
        elif (
            kind == "exposure" and brightness == contrast == 0 and tonal_defaults and points is None
        ):
            command, params = "exposure", {"exposure": exposure, "offset": offset, "gamma": gamma}
        elif (
            kind == "levels"
            and brightness == contrast == exposure == offset == 0
            and points is None
        ):
            command, params = (
                "levels",
                {
                    "inBlack": in_black,
                    "inWhite": in_white,
                    "outBlack": out_black,
                    "outWhite": out_white,
                    "gamma": gamma,
                },
            )
        elif (
            kind == "curves"
            and brightness == contrast == exposure == offset == 0
            and gamma == 1
            and tonal_defaults
        ):
            if not isinstance(points, list) or not 2 <= len(points) <= 19:
                raise AdapterError("invalid_arguments")
            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    raise AdapterError("invalid_arguments")
                for v in point:
                    number(v, 0, 255, True)
            if any(a[0] >= b[0] for a, b in zip(points, points[1:])):
                raise AdapterError("invalid_arguments")
            command, params = "curves", {"points": points}
        else:
            raise AdapterError("arguments_not_applicable_to_kind")
        self._layer_budget()
        result = self._edit("layer.newAdjustmentLayer." + command, params)
        new_id = result["command_result"]["layer"]
        self._verify(find_layer(result["document"], new_id)[0].get("kind") == "Adjustment")
        result.update(layer=new_id, verified=True)
        return result

    def fill_layer(self, color, name="Fill", end_color=None, angle=0, style="linear"):
        _hex_color(color)
        _name(name)
        number(angle, -180, 180)
        enum(style, {"linear", "radial", "angle", "reflected", "diamond"})
        if end_color is not None:
            _hex_color(end_color)
        elif angle != 0 or style != "linear":
            raise AdapterError("arguments_not_applicable_to_kind")
        doc = self._layer_budget()
        budget(doc, extra_pixels=doc["width"] * doc["height"])
        command = "solidColor" if end_color is None else "gradient"
        params = (
            {"color": color}
            if end_color is None
            else {"from": color, "to": end_color, "angle": angle, "style": style, "reverse": False}
        )
        result = self._edit("layer.newFillLayer." + command, params)
        new_id = result["command_result"]["layer"]
        result = self._edit("layer.setProps", {"layer": new_id, "name": name})
        self._verify(find_layer(result["document"], new_id)[0].get("name") == name)
        result.update(layer=new_id, verified=True)
        return result

    def layer_group(self, name):
        _name(name)
        self._layer_budget()
        result = self._edit("layer.new.group", {"name": name})
        new_id = result["command_result"]["layer"]
        self._verify(find_layer(result["document"], new_id)[0].get("kind") == "Group")
        result.update(layer=new_id, verified=True)
        return result

    def layer_delete(self, layer):
        identity(layer)
        find_layer(self._inspect(), layer)
        result = self._edit("layer.delete", {"layer": layer})
        self._verify(layer not in {v["id"] for v, _ in layers(result["document"])})
        result["verified"] = True
        return result

    def selection_all(self):
        doc = self._inspect()
        budget(doc, extra_pixels=doc["width"] * doc["height"])
        result = self._edit("select.all", {})
        self._verify(
            result["document"].get("selectionBounds") == [0, 0, doc["width"], doc["height"]]
        )
        result["verified"] = True
        return result

    def selection_invert(self):
        doc = self._inspect()
        if not doc.get("hasSelection"):
            raise AdapterError("selection_required")
        budget(doc, extra_pixels=doc["width"] * doc["height"])
        return self._edit("select.inverse", {})

    def selection_fill(self, layer, color, opacity=1):
        identity(layer)
        _hex_color(color)
        number(opacity, 0, 1)
        doc = self._inspect()
        item, _ = find_layer(doc, layer)
        if item.get("kind") != "Pixel":
            raise AdapterError("fill_pixel_only")
        if doc.get("activeLayer") != layer or doc.get("selectedLayers") != [layer]:
            raise AdapterError("single_active_layer_required")
        if not doc.get("hasSelection"):
            raise AdapterError("selection_required")
        x, y, w, h = bounds(doc.get("selectionBounds"))
        if x < 0 or y < 0 or x + w > doc["width"] or y + h > doc["height"]:
            raise AdapterError("selection_outside_canvas")
        budget(doc, extra_pixels=w * h)
        combined = union_bounds(item.get("bounds"), [x, y, w, h])
        old = bounds(item["bounds"])
        budget(doc, extra_pixels=max(0, combined[2] * combined[3] - old[2] * old[3]))
        rgba = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)] + [opacity]
        # v0.2.0's solid fill ignores the documented opacity parameter; use
        # the color's supported alpha channel instead.
        return self._edit("edit.fill", {"layer": layer, "color": rgba, "target": "pixels"})

    def document_sample_pixel(self, x, y):
        number(x, 0, 4095, True)
        number(y, 0, 4095, True)
        doc = self._inspect()
        budget(doc)
        if x >= doc["width"] or y >= doc["height"]:
            raise AdapterError("sample_outside_canvas")
        rgba = self._json("command_run", {"id": "document.pixel", "params": {"x": x, "y": y}})
        if not isinstance(rgba, list) or len(rgba) != 4:
            raise AdapterError("invalid_upstream_result")
        for v in rgba:
            number(v, -65536, 65536)
        return {"x": x, "y": y, "rgba": rgba, "source": "native_composite"}

    def _noise_target(self, layer):
        identity(layer)
        doc = self._inspect()
        item, _ = find_layer(doc, layer)
        if item.get("kind") != "Pixel":
            raise AdapterError("noise_pixel_only")
        if doc.get("activeLayer") != layer or doc.get("selectedLayers") != [layer]:
            raise AdapterError("single_active_layer_required")
        channels = doc.get("channels", [])
        if channels and (
            not isinstance(channels, dict)
            or channels.get("alpha")
            or channels.get("quickMask") is not None
            or channels.get("target") != {"kind": "composite"}
        ):
            raise AdapterError("noise_composite_target_required")
        if doc.get("quickMask"):
            raise AdapterError("noise_composite_target_required")
        rectangles = [item.get("bounds")]
        if doc.get("hasSelection"):
            rectangles.append(doc.get("selectionBounds"))
        for rectangle in rectangles:
            x, y, w, h = bounds(rectangle)
            if x < 0 or y < 0 or x + w > doc["width"] or y + h > doc["height"]:
                raise AdapterError("noise_content_outside_canvas")
        # Fixed neighbourhood filters may allocate a canvas-sized work buffer.
        budget(doc, extra_pixels=doc["width"] * doc["height"])
        return doc

    def noise_reduce(
        self,
        layer,
        strength=6,
        preserve_details=60,
        reduce_color_noise=45,
        sharpen_details=25,
        remove_jpeg_artifact=False,
    ):
        number(strength, 0, 10)
        for value in (preserve_details, reduce_color_noise, sharpen_details):
            number(value, 0, 100)
        if not isinstance(remove_jpeg_artifact, bool):
            raise AdapterError("invalid_arguments")
        self._noise_target(layer)
        return self._edit(
            "filter.noise.reduceNoise",
            {
                "layer": layer,
                "strength": strength,
                "preserveDetails": preserve_details,
                "reduceColorNoise": reduce_color_noise,
                "sharpenDetails": sharpen_details,
                "removeJpegArtifact": remove_jpeg_artifact,
            },
        )

    def noise_despeckle(self, layer):
        self._noise_target(layer)
        return self._edit("filter.noise.despeckle", {"layer": layer})

    def selection_from_mask(self, layer, invert=False):
        identity(layer)
        if not isinstance(invert, bool):
            raise AdapterError("invalid_arguments")
        doc = self._inspect()
        item, _ = find_layer(doc, layer)
        if not item.get("hasMask"):
            raise AdapterError("mask_required")
        # The native loadSelection handler reads exactly the current canvas;
        # no external document or arbitrary channel is accepted here.
        budget(doc, extra_pixels=doc["width"] * doc["height"])
        return self._edit(
            "select.loadSelection",
            {"channel": "mask", "layer": layer, "operation": "new", "invert": invert},
        )
