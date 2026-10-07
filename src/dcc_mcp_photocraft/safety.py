"""Conservative budgets for the surfaces reported by PhotoCraft 0.2.0."""

import math

from .errors import AdapterError
from .paths import MAX_PIXELS

MAX_LAYERS = 64
MAX_DEPTH = 8
MAX_TOTAL_PIXELS = 4 * MAX_PIXELS
MAX_COORD = 8192


def bounds(value):
    if not isinstance(value, list) or len(value) != 4:
        raise AdapterError("unverifiable_geometry")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise AdapterError("unverifiable_geometry")
    x, y, w, h = value
    if w < 0 or h < 0 or w * h > MAX_PIXELS:
        raise AdapterError("resource_limit")
    if max(abs(x), abs(y), abs(x + w), abs(y + h)) > MAX_COORD:
        raise AdapterError("resource_limit")
    return x, y, w, h


def layers(doc):
    result, seen = [], set()

    def visit(items, parent=None, depth=1):
        if not isinstance(items, list) or (items and depth > MAX_DEPTH):
            raise AdapterError("layer_limit")
        for item in items:
            if not isinstance(item, dict):
                raise AdapterError("invalid_upstream_result")
            identity = item.get("id")
            if isinstance(identity, bool) or not isinstance(identity, int) or identity in seen:
                raise AdapterError("invalid_upstream_result")
            seen.add(identity)
            result.append((item, parent))
            if len(result) > MAX_LAYERS:
                raise AdapterError("layer_limit")
            if "children" in item:
                visit(item["children"], identity, depth + 1)

    visit(doc.get("layers", []))
    return result


def surface_pixels(layer):
    value = layer.get("bounds")
    pixels = 0 if value is None else math.prod(bounds(value)[2:])
    # Native inspection does not report mask bounds. Only bounded adapter
    # operations can create masks; reserve the entire allowed surface budget.
    return pixels + (MAX_PIXELS if layer.get("hasMask") else 0)


def budget(doc, extra_pixels=0, extra_layers=0):
    width, height = doc.get("width"), doc.get("height")
    if any(
        isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 4096 for v in (width, height)
    ):
        raise AdapterError("resource_limit")
    tree = layers(doc)
    if len(tree) + extra_layers > MAX_LAYERS:
        raise AdapterError("layer_limit")
    total = sum(surface_pixels(item) for item, _ in tree) + extra_pixels
    total += sum(
        width * height
        for item, _ in tree
        if item.get("kind") == "Fill" and item.get("bounds") is None
    )
    if doc.get("hasSelection"):
        total += math.prod(bounds(doc.get("selectionBounds"))[2:])
    if total > MAX_TOTAL_PIXELS:
        raise AdapterError("resource_limit")
    return tree


def find_layer(doc, layer):
    for item, parent in layers(doc):
        if item["id"] == layer:
            return item, parent
    raise AdapterError("layer_not_found")


def geometry(doc, layer=None):
    tree = budget(doc)
    channel_state = doc.get("channels", [])
    channels = bool(channel_state)
    if isinstance(channel_state, dict):
        channels = (
            bool(channel_state.get("alpha"))
            or channel_state.get("quickMask") is not None
            or channel_state.get("target") != {"kind": "composite"}
        )
    if doc.get("hasSelection") or doc.get("quickMask") or channels:
        raise AdapterError("geometry_selection_or_channel")
    selected = tree if layer is None else [find_layer(doc, layer)]
    for item, _ in selected:
        if item.get("kind") != "Pixel":
            raise AdapterError("geometry_pixel_only")
        if item.get("hasMask"):
            raise AdapterError("geometry_mask_bounds_unavailable")
        if item.get("linkGroup") is not None:
            raise AdapterError("geometry_linked_layers")
        if item.get("effects") or item.get("video"):
            raise AdapterError("geometry_unsupported_layer_state")
        bounds(item.get("bounds"))
    return selected


def resize_budget(doc, width, height):
    tree = geometry(doc)
    sx, sy = width / doc["width"], height / doc["height"]
    total = 0
    for item, _ in tree:
        x, y, w, h = bounds(item["bounds"])
        if not w or not h:
            continue
        # Native resampling scales each surface's actual off-canvas bounds.
        left, top = math.floor(x * sx), math.floor(y * sy)
        right, bottom = math.ceil((x + w) * sx), math.ceil((y + h) * sy)
        bounds([left, top, right - left, bottom - top])
        total += (right - left) * (bottom - top)
    if total > MAX_TOTAL_PIXELS:
        raise AdapterError("resource_limit")


def affine_bounds(source, matrix):
    x, y, w, h = bounds(source)
    a, b, c, d, e, f = matrix
    points = [
        (a * u + c * v + e, b * u + d * v + f)
        for u, v in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    ]
    left = math.floor(min(p[0] for p in points))
    top = math.floor(min(p[1] for p in points))
    right = math.ceil(max(p[0] for p in points))
    bottom = math.ceil(max(p[1] for p in points))
    return bounds([left, top, right - left, bottom - top])


def union_bounds(first, second):
    x, y, w, h = bounds(first)
    a, b, c, d = bounds(second)
    if not w or not h:
        return bounds(second)
    if not c or not d:
        return bounds(first)
    left, top = min(x, a), min(y, b)
    return bounds([left, top, max(x + w, a + c) - left, max(y + h, b + d) - top])
