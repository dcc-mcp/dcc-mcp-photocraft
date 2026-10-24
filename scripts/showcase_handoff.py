"""PhotoCraft showcase evidence receipt, not a shared Core asset protocol."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_png(path: Path) -> dict:
    """Independently decode PNG samples and report actual color metadata."""
    from PIL import Image

    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("handoff requires PNG bytes")
    offset, chunks, depth = 8, {}, None
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + length + 12
        if end > len(data):
            raise ValueError("truncated PNG chunk")
        kind, payload = data[offset + 4 : offset + 8], data[offset + 8 : end - 4]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != struct.unpack(">I", data[end - 4 : end])[0]:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            depth = payload[8]
        if kind in (b"iCCP", b"sRGB", b"gAMA", b"cHRM"):
            name = kind.decode("ascii")
            if name in chunks:
                raise ValueError("duplicate PNG color chunk")
            chunks[name] = {
                "bytes": length,
                "sha256": _sha(payload),
                "payload_hex": payload.hex() if kind != b"iCCP" else None,
            }
        offset = end
        if kind == b"IEND":
            break
    with Image.open(path) as image:
        image.load()
        if image.format != "PNG" or image.mode not in ("RGB", "RGBA") or depth != 8:
            raise ValueError("showcase handoff supports only decoded RGB8/RGBA8 PNG")
        return {
            "bytes": len(data),
            "sha256": _sha(data),
            "format": "PNG",
            "width": image.width,
            "height": image.height,
            "samples": image.mode + "8",
            "alpha": {
                "present": image.mode == "RGBA",
                "extrema": list(image.getchannel("A").getextrema()) if image.mode == "RGBA" else None,
            },
            "decoded_pixel_sha256": _sha(image.tobytes()),
            "decoded_pixel_layout": (
                "row-major interleaved RGB/RGBA unsigned 8-bit encoded samples; no color conversion"
            ),
            "color_chunks": {
                name: {"present": name in chunks, **chunks.get(name, {})} for name in ("iCCP", "sRGB", "gAMA", "cHRM")
            },
            "icc_profile_sha256": _sha(image.info["icc_profile"]) if image.info.get("icc_profile") else None,
            "color_tagging": "untagged"
            if not chunks
            else "PNG color metadata present; consumer interpretation pending",
        }


def write_handoff(output: Path, *, synthetic: bool) -> dict:
    """Write only after the enclosing PhotoCraft acceptance has passed."""
    images = []
    for directory, role in (("inputs", "input"), ("artifacts", "output")):
        for path in sorted((output / directory).rglob("*.png")):
            if path.is_file() and not path.is_symlink():
                images.append({"path": path.relative_to(output).as_posix(), "role": role, **inspect_png(path)})
    projects = [
        {
            "path": path.relative_to(output).as_posix(),
            "sha256": _sha(path.read_bytes()),
            "bytes": path.stat().st_size,
            "format": "pcraft",
            "consumer": "PhotoCraft only; editable native project",
        }
        for path in sorted((output / "artifacts").rglob("*.pcraft"))
        if path.is_file() and not path.is_symlink()
    ]
    receipt = {
        "receipt_kind": "PhotoCraft showcase evidence; not a shared Core protocol",
        "inputs": "original synthetic geometry"
        if synthetic
        else "operator-supplied PNG; synthetic provenance not claimed",
        "color_intent": "synthetic RGB numbers intended as sRGB" if synthetic else "operator intent unspecified",
        "color_boundary": (
            "Untagged PNG requires explicit consumer sRGB assignment for the synthetic intent. "
            "No color-managed interchange, scene-linear data, OCIO or EXR conversion is claimed."
        ),
        "photocraft_validation": {
            "status": "passed",
            "evidence": "report.json",
            "scope": "this script's native save/close/reopen and pixel readbacks only",
        },
        "consumers": {name: {"status": "pending", "readback": False} for name in ("FilmCraft", "CAD-DCC", "Nuke")},
        "images": images,
        "native_projects": projects,
    }
    (output / "handoff.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
