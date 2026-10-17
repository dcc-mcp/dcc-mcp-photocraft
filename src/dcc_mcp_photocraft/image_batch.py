"""Small typed image workflows in one owned native MCP process.

The operation catalog below is adapter policy, not a mirror of upstream's
command table. Native tool schemas and required command existence are checked
at runtime. Command parameter docs are prose, so parameters are validated by
this wrapper's bounded schema, not claimed to have upstream JSON schemas.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import stat
import struct
import subprocess
import threading
import time
import uuid
import zlib
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .cli_transport import CliError, CliTransport

MAX_STEPS = 48
MAX_DIMENSION = 4096
MAX_PIXELS = 4_194_304
MAX_LAYER_PIXELS = 16_777_216
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_BATCH_BYTES = 256 * 1024
MAX_REPLY_BYTES = 8 * 1024 * 1024
MAX_TOTAL_REPLY_BYTES = 64 * 1024 * 1024
MAX_DURATION_SECS = 120.0
MAX_LAYERS = 16
_ALIAS = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,47}$"}
_PATH = {"type": "string", "minLength": 1, "maxLength": 240}
_COORD = {"type": "number", "minimum": 0, "maximum": MAX_DIMENSION}


def _step(op: str, fields: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"op": {"const": op}, "document": _ALIAS, **fields},
        "required": ["op", "document", *required],
    }


INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_STEPS,
            "items": {
                "oneOf": [
                    _step("open_png", {"input": _PATH, "base_layer": _ALIAS}, ["input", "base_layer"]),
                    _step(
                        "place_png",
                        {
                            "input": _PATH,
                            "layer": _ALIAS,
                            "center": {"type": "array", "items": _COORD, "minItems": 2, "maxItems": 2},
                            "scale": {"type": "number", "exclusiveMinimum": 0, "maximum": 400},
                        },
                        ["input", "layer", "center", "scale"],
                    ),
                    _step(
                        "set_layer",
                        {
                            "layer": _ALIAS,
                            "name": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                                "pattern": "^[^\\x00-\\x1f]+$",
                            },
                            "visible": {"type": "boolean"},
                            "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        ["layer"],
                    ),
                    _step(
                        "mask_rectangle",
                        {
                            "layer": _ALIAS,
                            "x": {"type": "integer", "minimum": 0, "maximum": MAX_DIMENSION},
                            "y": {"type": "integer", "minimum": 0, "maximum": MAX_DIMENSION},
                            "width": {"type": "integer", "minimum": 1, "maximum": MAX_DIMENSION},
                            "height": {"type": "integer", "minimum": 1, "maximum": MAX_DIMENSION},
                            "ellipse": {"type": "boolean"},
                        },
                        ["layer", "x", "y", "width", "height"],
                    ),
                    _step(
                        "set_mask",
                        {
                            "layer": _ALIAS,
                            "density": {"type": "number", "minimum": 0, "maximum": 100},
                            "feather": {"type": "number", "minimum": 0, "maximum": 32},
                            "enabled": {"type": "boolean"},
                        },
                        ["layer"],
                    ),
                    _step(
                        "curves",
                        {
                            "layer": _ALIAS,
                            "points": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 8,
                                "items": {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "items": {"type": "integer", "minimum": 0, "maximum": 255},
                                },
                            },
                        },
                        ["layer", "points"],
                    ),
                    _step("inspect", {}, []),
                    _step("save_project", {"output": _PATH}, ["output"]),
                    _step("close", {}, []),
                    _step("reopen_project", {"output": _PATH}, ["output"]),
                    _step("export_png", {"output": _PATH}, ["output"]),
                ]
            },
        }
    },
}

# Only reviewed translations; their existence is resolved in the live catalog.
_COMMANDS = {
    "set_layer": {"layer.setProps": ("layer",)},
    "mask_rectangle": {
        "select.rect": ("x", "y", "width", "height", "ellipse"),
        "layer.layerMask.revealSelection": ("layer",),
        "select.deselect": (),
    },
    "set_mask": {
        "layer.layerMask.edit": ("layer", "density", "feather"),
        "layer.layerMask.enabled": ("layer", "enabled"),
    },
    "curves": {"layer.newAdjustmentLayer.curves": ("points",)},
}


def secure_publication_supported() -> bool:
    """Fail closed where descriptor-relative no-follow access is unavailable."""
    return (
        os.name != "nt"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.link in os.supports_dir_fd
    )


def _logical_path(value: str, suffix: str) -> tuple[str, ...]:
    # POSIX logical names on every platform; reject Windows path forms too.
    if "\\" in value or ":" in value or any(ord(char) < 32 for char in value):
        raise CliError("paths must be plain root-relative logical file names")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts) or Path(value).suffix.lower() != suffix:
        raise CliError(f"path must be root-relative, without traversal, and end in {suffix}")
    return parts


def _directory_fd(path: Path, stack: ExitStack) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    stack.callback(os.close, descriptor)
    return descriptor


def _relative_parent(root_fd: int, parts: tuple[str, ...], stack: ExitStack) -> int:
    descriptor = os.dup(root_fd)
    stack.callback(os.close, descriptor)
    for part in parts[:-1]:
        descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        stack.callback(os.close, descriptor)
    return descriptor


def _read_regular(parent: int, name: str, limit: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise CliError("file must be a nonempty regular file within the byte limit")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(result) > limit or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise CliError("file exceeded its byte limit or changed while being read")
        return result
    finally:
        os.close(descriptor)


def _png(data: bytes) -> dict[str, Any]:
    """Validate bounded noninterlaced 8-bit RGB/RGBA PNG and read its pixels.

    Restricting imports to this explicit subset avoids accepting native projects,
    animation, exotic decoders or unbounded decompression as image inputs.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CliError("input/export is not a PNG")
    offset, compressed, header, ended = 8, bytearray(), None, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise CliError("truncated PNG")
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        if size > MAX_INPUT_BYTES or offset + 12 + size > len(data):
            raise CliError("invalid PNG chunk")
        payload = data[offset + 8 : offset + 8 + size]
        crc = struct.unpack(">I", data[offset + 8 + size : offset + 12 + size])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise CliError("PNG checksum failed")
        if header is None and kind != b"IHDR":
            raise CliError("PNG header must be first")
        if kind == b"IHDR":
            if header is not None or len(payload) != 13:
                raise CliError("invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION or width * height > MAX_PIXELS:
                raise CliError("PNG exceeds the dimension or pixel budget")
            if depth != 8 or color not in {2, 6} or compression or filtering or interlace:
                raise CliError("only noninterlaced 8-bit RGB/RGBA PNG is supported")
            header = (width, height, 3 if color == 2 else 4)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            if size or offset + 12 != len(data):
                raise CliError("invalid PNG end or trailing data")
            ended = True
        elif kind not in {b"sRGB", b"gAMA", b"cHRM", b"pHYs"}:
            # Do not pass compressed profiles/text or arbitrary metadata to the
            # native decoder: byte limits alone do not bound their inflation.
            raise CliError("unsupported PNG chunk or metadata")
        elif size != {b"sRGB": 1, b"gAMA": 4, b"cHRM": 32, b"pHYs": 9}[kind]:
            raise CliError("invalid fixed-size PNG metadata")
        offset += size + 12
    if not ended or header is None or not compressed:
        raise CliError("incomplete PNG")
    width, height, channels = header
    stride = width * channels
    expected = (stride + 1) * height
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        raise CliError("invalid PNG compression") from exc
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise CliError("PNG decompressed length does not match its bounded dimensions")
    digest = hashlib.sha256()
    previous = bytearray(stride)
    samples = []
    positions = {(0, 0), (width // 2, height // 2), (width - 1, height - 1)}
    for y in range(height):
        mode = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        if mode > 4:
            raise CliError("invalid PNG filter")
        for x in range(stride):
            a, b, c = (
                row[x - channels] if x >= channels else 0,
                previous[x],
                previous[x - channels] if x >= channels else 0,
            )
            if mode == 1:
                predictor = a
            elif mode == 2:
                predictor = b
            elif mode == 3:
                predictor = (a + b) // 2
            elif mode == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
            else:
                predictor = 0
            row[x] = (row[x] + predictor) & 255
        rgba = (
            row
            if channels == 4
            else bytearray(channel for x in range(width) for channel in (*row[x * 3 : x * 3 + 3], 255))
        )
        digest.update(rgba)
        for x, sy in sorted(positions):
            if sy == y:
                samples.append({"x": x, "y": y, "rgba": list(rgba[x * 4 : x * 4 + 4])})
        previous = row
    return {"width": width, "height": height, "rgba_sha256": digest.hexdigest(), "samples": samples}


def _validate(steps: Any) -> list[dict[str, Any]]:
    try:
        serialized = json.dumps(steps, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CliError("steps must contain finite JSON values") from exc
    if len(serialized.encode("utf-8")) > MAX_BATCH_BYTES:
        raise CliError("batch exceeds the 256 KiB request budget")
    try:
        Draft202012Validator(INPUT_SCHEMA).validate({"steps": steps})
    except ValidationError as exc:
        raise CliError("batch does not match the reviewed typed image-operation schema") from exc
    # Copy JSON values so caller changes cannot mutate the preflighted batch.
    result = json.loads(serialized)
    documents, layers, saved, outputs, masks = {}, {}, {}, set(), {}
    active = None
    for step in result:
        op, doc = step["op"], step["document"]
        if op == "open_png":
            if active is not None or doc in documents:
                raise CliError("open_png needs a new document alias and an empty session")
            documents[doc] = True
            layers[doc] = {step["base_layer"]}
            masks[doc] = {step["base_layer"]: False}
            active = doc
        elif op == "reopen_project":
            if active is not None or saved.get(step["output"], {}).get("document") != doc:
                raise CliError("reopen_project needs an empty session and this document's own saved output")
            layers[doc] = set(saved[step["output"]]["layers"])
            masks[doc] = dict(saved[step["output"]]["masks"])
            active = doc
        elif active != doc:
            raise CliError("operation refers to an unknown or closed document alias")
        if op in {"open_png", "place_png"}:
            _logical_path(step["input"], ".png")
        if op in {"place_png", "curves"}:
            if step["layer"] in layers[doc] or len(layers[doc]) >= MAX_LAYERS:
                raise CliError("new layer alias is repeated or the layer budget is exhausted")
            layers[doc].add(step["layer"])
            masks[doc][step["layer"]] = op == "curves"
        if op in {"set_layer", "mask_rectangle", "set_mask"} and step["layer"] not in layers[doc]:
            raise CliError("unknown layer alias")
        if op == "set_layer" and not set(step) & {"name", "visible", "opacity"}:
            raise CliError("set_layer needs at least one reviewed property")
        if op == "set_mask" and not set(step) & {"density", "feather", "enabled"}:
            raise CliError("set_mask needs at least one reviewed property")
        if op == "mask_rectangle":
            if masks[doc][step["layer"]]:
                raise CliError("mask_rectangle cannot replace an existing mask")
            masks[doc][step["layer"]] = True
        if op == "set_mask" and not masks[doc][step["layer"]]:
            raise CliError("set_mask requires an existing mask")
        if op == "curves":
            xs = [point[0] for point in step["points"]]
            if xs[0] != 0 or xs[-1] != 255 or any(a >= b for a, b in zip(xs, xs[1:])):
                raise CliError("curves need strictly increasing input coordinates with endpoints 0 and 255")
        if op in {"save_project", "export_png"}:
            name = step["output"]
            _logical_path(name, ".pcraft" if op == "save_project" else ".png")
            if name in outputs:
                raise CliError("output is repeated; overwrites are disabled")
            outputs.add(name)
            if op == "save_project":
                saved[name] = {"document": doc, "layers": set(layers[doc]), "masks": dict(masks[doc])}
        if op == "close":
            active = None
    return result


def _reject_refs(value: Any) -> None:
    if isinstance(value, dict):
        if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
            raise CliError("native schema references are unsupported")
        for child in value.values():
            _reject_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_refs(child)


def _native_failure(value: Any) -> bool:
    if isinstance(value, dict):
        if (
            value.get("ok") is False
            or value.get("success") is False
            or value.get("isError") is True
            or value.get("error")
        ):
            return True
        return any(_native_failure(child) for child in value.values())
    if isinstance(value, list):
        return any(_native_failure(child) for child in value)
    return False


class _NativeSession:
    """One bounded, non-retrying JSON-lines MCP session; no SDK/Python floor."""

    def __init__(self, transport: CliTransport, stage: Path, duration: float):
        self.deadline = time.monotonic() + duration
        self.sequence = 0
        self.total_bytes = 0
        self.buffer = b""
        self.poisoned = False
        self.schemas: dict[str, Any] = {}
        self.process = subprocess.Popen(
            [transport.binary, "mcp", "--automation-read-root", str(stage), "--automation-write-root", str(stage)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            env={**os.environ, "PHOTOCRAFT_CONFIG_DIR": str(stage / "config")},
        )

    def close(self) -> None:
        # Never wait indefinitely for a stuck command, stderr, or pipe reader.
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                except OSError:
                    pass
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

    def rpc(self, method: str, params: dict[str, Any], *, notification: bool = False) -> Any:
        if self.poisoned:
            raise CliError("native session is unusable; do not replay the batch")
        if time.monotonic() >= self.deadline:
            self.poisoned = True
            raise CliError("native batch deadline exceeded before dispatch; do not replay")
        self.sequence += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            request["id"] = self.sequence
        pending: queue.Queue[Any] = queue.Queue(maxsize=1)

        def exchange() -> None:
            try:
                if time.monotonic() >= self.deadline:
                    raise CliError("native batch deadline exceeded before dispatch; do not replay")
                wire = (json.dumps(request, allow_nan=False) + "\n").encode("utf-8")
                while wire:
                    written = self.process.stdin.write(wire)
                    if not written:
                        raise CliError("native input pipe closed")
                    wire = wire[written:]
                if notification:
                    pending.put((True, None))
                    return
                for _ in range(16):
                    while b"\n" not in self.buffer:
                        chunk = os.read(self.process.stdout.fileno(), 65536)
                        if not chunk:
                            raise CliError("native output pipe closed")
                        self.buffer += chunk
                        if len(self.buffer) > MAX_REPLY_BYTES:
                            raise CliError("native reply exceeded its byte budget")
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    self.total_bytes += len(line)
                    if not line or len(line) > MAX_REPLY_BYTES or self.total_bytes > MAX_TOTAL_REPLY_BYTES:
                        raise CliError("native reply exceeded its byte budget or ended unexpectedly")
                    value = json.loads(line)
                    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                        raise CliError("invalid native JSON-RPC reply")
                    if "id" not in value and "method" in value:
                        continue
                    if value.get("id") != request["id"] or "error" in value or "result" not in value:
                        raise CliError("native request failed or returned an unexpected reply identity")
                    pending.put((True, value["result"]))
                    return
                raise CliError("native notification budget exceeded")
            except BaseException as exc:
                pending.put((False, exc))

        worker = threading.Thread(target=exchange, daemon=True)
        worker.start()
        try:
            success, result = pending.get(timeout=max(0.001, self.deadline - time.monotonic()))
        except queue.Empty as exc:
            self.poisoned = True
            raise CliError("native batch deadline exceeded; mutation may have occurred; do not replay") from exc
        if not success:
            self.poisoned = True
            if isinstance(result, CliError):
                raise result
            raise CliError("native transport failed; mutation may have occurred; do not replay") from result
        return result

    def discover(self, steps: list[dict[str, Any]]) -> None:
        initialized = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "dcc-mcp-photocraft", "version": "1"},
            },
        )
        if not isinstance(initialized, dict) or not isinstance(initialized.get("capabilities", {}).get("tools"), dict):
            raise CliError("native MCP tool capability is unavailable")
        self.rpc("notifications/initialized", {}, notification=True)
        catalog, cursor = [], None
        for _ in range(8):
            result = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise CliError("invalid native tool catalog")
            catalog.extend(result["tools"])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            raise CliError("native tool catalog exceeded the pagination budget")
        for tool in catalog:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or tool["name"] in self.schemas:
                raise CliError("ambiguous native tool catalog")
            self.schemas[tool["name"]] = tool.get("inputSchema")
        needed = {"session_list", "doc_inspect", "doc_open", "doc_close"}
        names = {"place_png": "doc_place", "save_project": "doc_save", "export_png": "doc_export"}
        commands: dict[str, set[str]] = {}
        for step in steps:
            if step["op"] in names:
                needed.add(names[step["op"]])
            for command, fields in _COMMANDS.get(step["op"], {}).items():
                commands.setdefault(command, set()).update(fields)
            if step["op"] == "set_layer":
                commands["layer.setProps"].update(key for key in ("name", "visible", "opacity") if key in step)
        if commands:
            needed.update({"command_run", "command_list"})
        for name in needed:
            schema = self.schemas.get(name)
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise CliError(f"required native tool {name} is unavailable or schema-incompatible")
            _reject_refs(schema)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise CliError(f"native tool {name} has an invalid schema") from exc
        if commands:
            listed = self.call("command_list", {})
            if not isinstance(listed, list):
                raise CliError("native command catalog is not a list")
            live = {}
            for item in listed:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in live:
                    raise CliError("native command catalog contains invalid or duplicate identities")
                live[item["id"]] = item
            for name, fields in commands.items():
                docs = live.get(name, {}).get("params")
                if not isinstance(docs, str) or any(f'"{field}"' not in docs for field in fields):
                    raise CliError(f"reviewed command {name} is unavailable or its documented parameters changed")
        # Preflight every argument shape before the first document is opened.
        self.validate_call("session_list", {})
        self.validate_call("doc_inspect", {"index": 0})
        for step in steps:
            for name, args in _calls_for_schema(step):
                self.validate_call(name, args)

    def validate_call(self, name: str, arguments: dict[str, Any]) -> None:
        schema = self.schemas.get(name)
        if not isinstance(schema, dict):
            raise CliError(f"native tool {name} is unavailable")
        # Properties must be explicitly described, not accepted by an open schema.
        if any(key not in schema.get("properties", {}) for key in arguments):
            raise CliError(f"native tool {name} no longer describes a required argument")
        try:
            Draft202012Validator(schema).validate(arguments)
        except (ValidationError, SchemaError) as exc:
            raise CliError(f"native tool {name} rejected the reviewed argument schema") from exc

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.validate_call(name, arguments)
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict) or _native_failure(result):
            raise CliError(f"native tool {name} reported a failure; do not replay")
        if "structuredContent" in result:
            value = result["structuredContent"]
        else:
            content = result.get("content")
            if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
                raise CliError(f"native tool {name} returned an unsupported result")
            try:
                value = json.loads(content[0]["text"])
            except (ValueError, KeyError, TypeError) as exc:
                raise CliError(f"native tool {name} returned invalid JSON content") from exc
        if _native_failure(value):
            raise CliError(f"native tool {name} reported a nested failure; do not replay")
        return value

    def command(self, name: str, params: dict[str, Any]) -> Any:
        result = self.call("command_run", {"id": name, "params": params, "wait": True})
        if isinstance(result, dict) and "job" in result:
            raise CliError("native command unexpectedly started a background job; do not replay")
        return result


def _calls_for_schema(step: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    op = step["op"]
    if op in {"open_png", "reopen_project"}:
        return [("doc_open", {"path": "input.png" if op == "open_png" else "output.pcraft"})]
    if op == "place_png":
        return [
            (
                "doc_place",
                {"path": "input.png", "index": 0, "fit": False, "scale": step["scale"], "center": step["center"]},
            )
        ]
    if op in {"save_project", "export_png"}:
        tool, extension = ("doc_save", "pcraft") if op == "save_project" else ("doc_export", "png")
        return [(tool, {"path": f"output.{extension}", "format": extension, "index": 0})]
    if op in {"inspect", "close"}:
        return [("doc_inspect" if op == "inspect" else "doc_close", {"index": 0})]
    commands = []
    if op == "set_layer":
        commands = [
            (
                "layer.setProps",
                {"layer": 1, **{key: step[key] for key in ("name", "visible", "opacity") if key in step}},
            )
        ]
    elif op == "mask_rectangle":
        commands = [
            ("select.rect", {key: step[key] for key in ("x", "y", "width", "height", "ellipse") if key in step}),
            ("layer.layerMask.revealSelection", {"layer": 1}),
            ("select.deselect", {}),
        ]
    elif op == "set_mask":
        fields = {key: step[key] for key in ("density", "feather") if key in step}
        if fields:
            commands.append(("layer.layerMask.edit", {"layer": 1, **fields}))
        if "enabled" in step:
            commands.append(("layer.layerMask.enabled", {"layer": 1, "enabled": step["enabled"]}))
    elif op == "curves":
        commands = [("layer.newAdjustmentLayer.curves", {"points": step["points"]})]
    return [("command_run", {"id": command, "params": params, "wait": True}) for command, params in commands]


def _same_directory(path: Path, descriptor: int) -> None:
    with ExitStack() as stack:
        current = _directory_fd(path, stack)
        a, b = os.fstat(current), os.fstat(descriptor)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise CliError("an authorized directory changed during the batch")


def _stage_file(descriptor: int, name: str, content: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
    with os.fdopen(fd, "wb") as file:
        file.write(content)


def _prepare_files(
    steps: list[dict[str, Any]], read_root: Path, write_root: Path, stage: Path, stack: ExitStack
) -> tuple[dict[str, Any], dict[str, Any], int]:
    read_fd = _directory_fd(read_root, stack)
    write_fd = _directory_fd(write_root, stack)
    stage_fd = _directory_fd(stage, stack)
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    total = 0
    for step in steps:
        if step["op"] in {"open_png", "place_png"} and step["input"] not in inputs:
            name = step["input"]
            parts = _logical_path(name, ".png")
            parent = _relative_parent(read_fd, parts, stack)
            content = _read_regular(parent, parts[-1], MAX_INPUT_BYTES)
            total += len(content)
            if total > 128 * 1024 * 1024:
                raise CliError("aggregate PNG inputs exceed the 128 MiB budget")
            pixels = _png(content)
            staged = f"input-{len(inputs)}.png"
            _stage_file(stage_fd, staged, content)
            inputs[name] = {"stage": staged, "sha256": hashlib.sha256(content).hexdigest(), "pixels": pixels}
        if step["op"] in {"save_project", "export_png"}:
            name = step["output"]
            suffix = ".pcraft" if step["op"] == "save_project" else ".png"
            parts = _logical_path(name, suffix)
            parent = _relative_parent(write_fd, parts, stack)
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise CliError("output already exists; overwrites are disabled")
            outputs[name] = {
                "stage": f"output-{len(outputs)}{suffix}",
                "parent": parent,
                "parent_path": write_root.joinpath(*parts[:-1]),
                "name": parts[-1],
            }
    # Geometry and aggregate allocation budgets are checked before native open.
    sizes, saved_sizes, area = {}, {}, 0
    for step in steps:
        op, doc = step["op"], step["document"]
        if op == "open_png":
            pixels = inputs[step["input"]]["pixels"]
            sizes[doc] = (pixels["width"], pixels["height"])
            area = pixels["width"] * pixels["height"]
        elif op == "reopen_project":
            area = saved_sizes[step["output"]]
        if op in {"place_png", "mask_rectangle"}:
            width, height = sizes[doc]
            if op == "place_png":
                source = inputs[step["input"]]["pixels"]
                w, h = source["width"] * step["scale"] / 100, source["height"] * step["scale"] / 100
                x, y = step["center"]
                if min(w, h) < 1 or max(w, h) > MAX_DIMENSION or not w / 2 <= x <= width - w / 2:
                    raise CliError("placed image must fit inside the canvas and dimension budget")
                if not h / 2 <= y <= height - h / 2:
                    raise CliError("placed image must fit inside the canvas")
                # Embedded source and transformed pixel extent both consume memory.
                area += max(source["width"] * source["height"], math.ceil(w) * math.ceil(h))
            elif step["x"] + step["width"] > width or step["y"] + step["height"] > height:
                raise CliError("mask rectangle must fit inside the canvas")
        if op == "curves":
            width, height = sizes[doc]
            area += width * height
        if op == "mask_rectangle":
            width, height = sizes[doc]
            area += width * height
        if area > MAX_LAYER_PIXELS:
            raise CliError("aggregate layer/mask area exceeds the allocation budget")
        if op == "save_project":
            saved_sizes[step["output"]] = area
    _same_directory(read_root, read_fd)
    _same_directory(write_root, write_fd)
    _same_directory(stage, stage_fd)
    return inputs, outputs, stage_fd


def _inspection(session: _NativeSession) -> dict[str, Any]:
    state = session.call("doc_inspect", {"index": 0})
    if not isinstance(state, dict) or not isinstance(state.get("layers"), list):
        raise CliError("native document inspection is incompatible")
    layers = state["layers"]
    ids = [layer.get("id") for layer in layers if isinstance(layer, dict)]
    if len(ids) != len(layers) or not 1 <= len(ids) <= MAX_LAYERS:
        raise CliError("native document has an unexpected layer structure")
    if any(type(item) is not int or item < 0 for item in ids) or len(set(ids)) != len(ids):
        raise CliError("native layer identities are invalid or ambiguous")
    return state


def _signature(state: dict[str, Any]) -> list[dict[str, Any]]:
    fields = ("kind", "name", "bounds", "hasMask", "hasVectorMask", "opacity", "visible", "fill", "blend", "adjustment")
    return [{key: layer.get(key) for key in fields} for layer in state["layers"]]


def _receipt(state: dict[str, Any], aliases: dict[str, int]) -> dict[str, Any]:
    reverse = {value: key for key, value in aliases.items()}
    fields = ("kind", "name", "bounds", "hasMask", "hasVectorMask", "opacity", "visible", "adjustment")
    return {
        "width": state.get("width"),
        "height": state.get("height"),
        "revision": state.get("revision"),
        "layers": [
            {"layer": reverse.get(layer["id"]), **{key: layer[key] for key in fields if key in layer}}
            for layer in state["layers"]
        ],
    }


def _check_session(session: _NativeSession, expected: int) -> None:
    state = session.call("session_list", {})
    docs = state.get("documents") if isinstance(state, dict) else None
    if not isinstance(docs, list) or len(docs) != expected:
        raise CliError("native session contains unexpected documents")
    if expected and (docs[0].get("index") != 0 or state.get("active") != 0):
        raise CliError("native active-document identity changed")


def _check_owned(session: _NativeSession, aliases: dict[str, int], width: int, height: int) -> None:
    _check_session(session, 1)
    state = _inspection(session)
    if (state.get("width"), state.get("height")) != (width, height):
        raise CliError("owned document dimensions changed unexpectedly")
    if {layer["id"] for layer in state["layers"]} != set(aliases.values()) or state.get("hasSelection"):
        raise CliError("owned layer identities or selection state changed unexpectedly")


def _run_steps(
    session: _NativeSession,
    steps: list[dict[str, Any]],
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    stage_fd: int,
    report: dict[str, Any],
) -> None:
    aliases: dict[str, int] = {}
    saved: dict[str, Any] = {}
    width = height = total_output_bytes = 0
    _check_session(session, 0)
    for index, step in enumerate(steps):
        op, doc = step["op"], step["document"]
        report["failed_step"] = index + 1
        record: dict[str, Any] = {"step": index + 1, "op": op, "document": doc}
        if op not in {"open_png", "reopen_project"}:
            _check_owned(session, aliases, width, height)
        if op != "inspect":
            report["mutation_possible"] = True
        if op in {"open_png", "reopen_project"}:
            _check_session(session, 0)
            if op == "open_png":
                path = inputs[step["input"]]["stage"]
            else:
                path = outputs[step["output"]]["stage"]
                content = _read_regular(stage_fd, path, MAX_OUTPUT_BYTES)
                if hashlib.sha256(content).hexdigest() != saved[step["output"]]["sha256"]:
                    raise CliError("owned native project changed since it was saved")
            opened = session.call("doc_open", {"path": path})
            if not isinstance(opened, dict) or opened.get("index") != 0:
                raise CliError("native document did not open with the expected owned identity")
            _check_session(session, 1)
            state = _inspection(session)
            width, height = state.get("width"), state.get("height")
            if op == "open_png":
                pixels = inputs[step["input"]]["pixels"]
                if (width, height) != (pixels["width"], pixels["height"]) or len(state["layers"]) != 1:
                    raise CliError("native PNG import has an unexpected document structure")
                aliases = {step["base_layer"]: state["layers"][0]["id"]}
            else:
                snapshot = saved[step["output"]]
                if (width, height) != snapshot["size"] or _signature(state) != snapshot["signature"]:
                    raise CliError("owned native project reopened with different layer structure")
                aliases = {alias: state["layers"][position]["id"] for alias, position in snapshot["positions"].items()}
            record["inspection"] = _receipt(state, aliases)
        elif op == "place_png":
            result = session.call(
                "doc_place",
                {
                    "path": inputs[step["input"]]["stage"],
                    "index": 0,
                    "fit": False,
                    "scale": step["scale"],
                    "center": step["center"],
                },
            )
            if not isinstance(result, dict) or result.get("smartObject") is not True:
                raise CliError("native placement was not an embedded Smart Object")
            layer = result.get("layer")
            state = _inspection(session)
            if (
                type(layer) is not int
                or layer in aliases.values()
                or layer not in {item["id"] for item in state["layers"]}
            ):
                raise CliError("native placement returned an invalid owned-layer identity")
            aliases[step["layer"]] = layer
            record["layer"] = step["layer"]
        elif op == "set_layer":
            params = {key: step[key] for key in ("name", "visible", "opacity") if key in step}
            session.command("layer.setProps", {"layer": aliases[step["layer"]], **params})
        elif op == "mask_rectangle":
            state = _inspection(session)
            target = next(layer for layer in state["layers"] if layer["id"] == aliases[step["layer"]])
            if target.get("hasMask") or state.get("hasSelection"):
                raise CliError("mask creation requires an unmasked owned layer and no existing selection")
            session.command(
                "select.rect", {key: step[key] for key in ("x", "y", "width", "height", "ellipse") if key in step}
            )
            session.command("layer.layerMask.revealSelection", {"layer": aliases[step["layer"]]})
            session.command("select.deselect", {})
        elif op == "set_mask":
            state = _inspection(session)
            target = next(layer for layer in state["layers"] if layer["id"] == aliases[step["layer"]])
            if not target.get("hasMask"):
                raise CliError("set_mask requires an existing owned layer mask")
            params = {key: step[key] for key in ("density", "feather") if key in step}
            if params:
                session.command("layer.layerMask.edit", {"layer": target["id"], **params})
            if "enabled" in step:
                session.command("layer.layerMask.enabled", {"layer": target["id"], "enabled": step["enabled"]})
        elif op == "curves":
            result = session.command("layer.newAdjustmentLayer.curves", {"points": step["points"]})
            state = _inspection(session)
            layer = result.get("layer") if isinstance(result, dict) else None
            if type(layer) is not int or layer in aliases.values():
                raise CliError("native adjustment returned an invalid layer identity")
            candidates = [item for item in state["layers"] if item["id"] == layer]
            if (
                len(candidates) != 1
                or candidates[0].get("kind") != "Adjustment"
                or "Curves" not in candidates[0].get("adjustment", {})
            ):
                raise CliError("native curves operation did not create a nondestructive adjustment layer")
            aliases[step["layer"]] = layer
            record["layer"] = step["layer"]
        elif op == "inspect":
            record["inspection"] = _receipt(_inspection(session), aliases)
        elif op in {"save_project", "export_png"}:
            output = outputs[step["output"]]
            tool, extension = ("doc_save", "pcraft") if op == "save_project" else ("doc_export", "png")
            worst_case = MAX_OUTPUT_BYTES if op == "save_project" else width * height * 4 + height + 1024 * 1024
            if total_output_bytes + worst_case > MAX_TOTAL_OUTPUT_BYTES:
                raise CliError("remaining output budget cannot safely accommodate the next native write")
            session.call(tool, {"path": output["stage"], "format": extension, "index": 0})
            content = _read_regular(stage_fd, output["stage"], MAX_OUTPUT_BYTES)
            output["sha256"] = hashlib.sha256(content).hexdigest()
            output["bytes"] = len(content)
            total_output_bytes += len(content)
            if total_output_bytes > MAX_TOTAL_OUTPUT_BYTES:
                raise CliError("aggregate native outputs exceeded their byte budget")
            if op == "save_project":
                state = _inspection(session)
                positions = {layer["id"]: position for position, layer in enumerate(state["layers"])}
                saved[step["output"]] = {
                    "sha256": output["sha256"],
                    "size": (width, height),
                    "signature": _signature(state),
                    "positions": {alias: positions[layer] for alias, layer in aliases.items()},
                }
            else:
                pixels = _png(content)
                if (pixels["width"], pixels["height"]) != (width, height):
                    raise CliError("native export dimensions do not match the owned document")
                output["pixels"] = pixels
            record["output"] = step["output"]
        elif op == "close":
            session.call("doc_close", {"index": 0})
            _check_session(session, 0)
            aliases = {}
        if op != "close":
            _check_owned(session, aliases, width, height)
        report["steps"].append(record)
        report["completed_steps"] = index + 1
    report.pop("failed_step", None)


def _publish(outputs: dict[str, Any], stage: Path, stage_fd: int, report: dict[str, Any]) -> None:
    total = 0
    for output in outputs.values():
        before = os.stat(output["stage"], dir_fd=stage_fd, follow_symlinks=False)
        content = _read_regular(stage_fd, output["stage"], MAX_OUTPUT_BYTES)
        if hashlib.sha256(content).hexdigest() != output["sha256"]:
            raise CliError("staged output changed after verification")
        total += len(content)
        if total > MAX_TOTAL_OUTPUT_BYTES:
            raise CliError("aggregate output exceeds the 256 MiB budget")
        info = os.stat(output["stage"], dir_fd=stage_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise CliError("staged output identity changed during verification")
        if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
            raise CliError("staged output is unexpectedly linked or not a regular file")
        output["identity"] = (info.st_dev, info.st_ino)
    for logical, output in outputs.items():
        _same_directory(stage, stage_fd)
        _same_directory(output["parent_path"], output["parent"])
        before = os.stat(output["stage"], dir_fd=stage_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != output["identity"]:
            raise CliError("staged output identity changed before publication")
        os.link(
            output["stage"], output["name"], src_dir_fd=stage_fd, dst_dir_fd=output["parent"], follow_symlinks=False
        )
        # Record immediately: a later failed check must not hide partial publication.
        artifact = {"path": logical, "verified": False}
        report["outputs"].append(artifact)
        _same_directory(output["parent_path"], output["parent"])
        source = os.stat(output["stage"], dir_fd=stage_fd, follow_symlinks=False)
        target = os.stat(output["name"], dir_fd=output["parent"], follow_symlinks=False)
        if (
            not stat.S_ISREG(target.st_mode)
            or (source.st_dev, source.st_ino) != output["identity"]
            or (target.st_dev, target.st_ino) != output["identity"]
        ):
            raise CliError("published output identity changed; reconcile the reported outputs")
        artifact.update(verified=True, bytes=output["bytes"], sha256=output["sha256"])
        if "pixels" in output:
            artifact["pixels"] = output["pixels"]


def _remove_stage(parent: int, name: str, expected: tuple[int, int]) -> None:
    """Remove only our inode; leave a replacement directory entirely untouched."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError:
        return
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != expected:
            return
        with os.scandir(descriptor) as entries:
            for entry in entries:
                try:
                    child = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(child.st_mode):
                        _remove_stage(descriptor, entry.name, (child.st_dev, child.st_ino))
                    else:
                        os.unlink(entry.name, dir_fd=descriptor)
                except OSError:
                    pass
        # Never rmdir a different directory installed at our original name.
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == expected:
            os.rmdir(name, dir_fd=parent)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def execute_image_batch(transport: CliTransport, steps: Any) -> dict[str, Any]:
    """Preflight, execute once, then securely publish newly named artifacts.

    Only one document can be open at a time, with at most sixteen owned layers.
    Every input is copied from the independent read authority into a private
    stage. The native process receives only that stage as its read/write roots;
    external native projects and caller-provided host identifiers never enter it.
    Failures after dispatch return a partial-state report and are never replayed.
    """
    prepared = _validate(steps)
    if not transport.read_root or not transport.write_root:
        raise CliError("image batches require independent configured PHOTOCRAFT_READ_ROOT and PHOTOCRAFT_WRITE_ROOT")
    if not secure_publication_supported():
        raise CliError(
            "secure no-follow image batch publication is unavailable on this platform; no native process started"
        )
    duration = getattr(transport, "timeout_secs", getattr(transport, "_timeout_secs", MAX_DURATION_SECS))
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise CliError("native timeout must be a positive finite number")
    duration = min(duration, MAX_DURATION_SECS)
    read_root = Path(os.path.abspath(transport.read_root))
    write_root = Path(os.path.abspath(transport.write_root))
    report: dict[str, Any] = {
        "ok": False,
        "status": "failed",
        "completed_steps": 0,
        "mutation_possible": False,
        "retry_safe": False,
        "steps": [],
        "outputs": [],
    }
    with ExitStack() as stack:
        try:
            write_fd = _directory_fd(write_root, stack)
            stage_name = ".photocraft-batch-" + uuid.uuid4().hex
            os.mkdir(stage_name, 0o700, dir_fd=write_fd)
            stage = write_root / stage_name
            stage_info = os.stat(stage_name, dir_fd=write_fd, follow_symlinks=False)
            stack.callback(_remove_stage, write_fd, stage_name, (stage_info.st_dev, stage_info.st_ino))
            _same_directory(write_root, write_fd)
            inputs, outputs, stage_fd = _prepare_files(prepared, read_root, write_root, stage, stack)
        except CliError:
            raise
        except OSError as exc:
            raise CliError(
                "authorized roots, inputs or output parents are unavailable or unsafe; no native process started"
            ) from exc
        session = None
        try:
            session = _NativeSession(transport, stage, duration)
            session.discover(prepared)
            _run_steps(session, prepared, inputs, outputs, stage_fd, report)
            session.close()
            session = None
            _publish(outputs, stage, stage_fd, report)
            report.update(ok=True, status="completed")
            report["inputs"] = [{"path": name, "sha256": item["sha256"]} for name, item in inputs.items()]
        except (CliError, OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            report["status"] = "partial" if report["mutation_possible"] or report["outputs"] else "failed"
            report["error"] = (
                str(exc) if isinstance(exc, CliError) else "native execution or publication failed; do not replay"
            )
        finally:
            if session is not None:
                session.close()
    return report
