"""Independent decoded samples and color facts for opt-in showcase receipts."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib

import pytest

from scripts.showcase_handoff import inspect_png, write_handoff

Image = pytest.importorskip("PIL.Image")
PngImagePlugin = pytest.importorskip("PIL.PngImagePlugin")


def test_untagged_rgba_keeps_encoded_samples_and_alpha(tmp_path):
    """No profile assignment or conversion may silently alter pixel evidence."""
    path = tmp_path / "original.png"
    image = Image.new("RGBA", (2, 1))
    image.putdata([(12, 34, 56, 0), (78, 90, 123, 128)])
    image.save(path)
    facts = inspect_png(path)
    assert facts["samples"] == "RGBA8"
    assert (facts["width"], facts["height"]) == (2, 1)
    assert facts["decoded_pixel_sha256"] == hashlib.sha256(image.tobytes()).hexdigest()
    assert facts["alpha"] == {"present": True, "extrema": [0, 128]}
    assert facts["color_tagging"] == "untagged"
    assert all(chunk == {"present": False} for chunk in facts["color_chunks"].values())
    assert facts["icc_profile_sha256"] is None


def test_receipt_preserves_actual_png_color_chunks(tmp_path):
    """Record raw color facts without treating gamma/chromaticity as a transform."""
    path = tmp_path / "tagged.png"
    metadata = PngImagePlugin.PngInfo()
    raw = {
        "sRGB": b"\0",
        "gAMA": struct.pack(">I", 45455),
        "cHRM": struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000),
    }
    for name, payload in raw.items():
        metadata.add(name.encode("ascii"), payload)
    Image.new("RGB", (3, 2), (10, 20, 30)).save(path, pnginfo=metadata)
    facts = inspect_png(path)
    for name, payload in raw.items():
        chunk = facts["color_chunks"][name]
        assert chunk["present"]
        assert chunk["payload_hex"] == payload.hex()
        assert chunk["sha256"] == hashlib.sha256(payload).hexdigest()
    assert facts["color_tagging"] == "PNG color metadata present; consumer interpretation pending"
    # PNG sRGB and iCCP are mutually exclusive; inspect the separate ICC case.
    profile = b"independent profile payload for metadata readback only"
    Image.new("RGB", (3, 2), (10, 20, 30)).save(path, icc_profile=profile)
    icc_facts = inspect_png(path)
    assert icc_facts["color_chunks"]["iCCP"]["present"]
    assert not icc_facts["color_chunks"]["sRGB"]["present"]
    assert icc_facts["icc_profile_sha256"] == hashlib.sha256(profile).hexdigest()


def test_handoff_keeps_other_consumers_pending_and_native_project_opaque(tmp_path):
    """A PhotoCraft receipt must not manufacture evidence for another host."""
    (tmp_path / "inputs").mkdir()
    (tmp_path / "artifacts").mkdir()
    Image.new("RGB", (2, 2), (11, 22, 33)).save(tmp_path / "inputs" / "original.png")
    Image.new("RGBA", (2, 2), (11, 22, 33, 128)).save(tmp_path / "artifacts" / "edited.png")
    project = tmp_path / "artifacts" / "editable.pcraft"
    project.write_bytes(b"opaque native project")
    receipt = write_handoff(tmp_path, synthetic=True)
    assert json.loads((tmp_path / "handoff.json").read_text()) == receipt
    assert {row["role"] for row in receipt["images"]} == {"input", "output"}
    assert receipt["color_intent"] == "synthetic RGB numbers intended as sRGB"
    assert "explicit consumer sRGB assignment" in receipt["color_boundary"]
    assert all(row == {"status": "pending", "readback": False} for row in receipt["consumers"].values())
    assert receipt["native_projects"][0]["sha256"] == hashlib.sha256(project.read_bytes()).hexdigest()
    assert "width" not in receipt["native_projects"][0]
    assert "unspecified" in write_handoff(tmp_path, synthetic=False)["color_intent"]


def test_corrupt_png_color_chunk_cannot_enter_receipt(tmp_path):
    """Raw chunk integrity is checked before Pillow's decoded observation."""
    path = tmp_path / "bad.png"
    payload = b"\0"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 1) + b"sRGB" + payload + struct.pack(">I", zlib.crc32(payload))
    )
    with pytest.raises(ValueError, match="CRC mismatch"):
        inspect_png(path)
