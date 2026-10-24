"""Readback evidence guards without running PhotoCraft or contacting GitHub."""

from __future__ import annotations

import json
import struct
import subprocess
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.native_readback import CoreClient, png_bytes, run


def test_original_png_has_valid_chunks_and_known_rgb_pixels():
    """Independently decode CRCs and scanlines from the synthetic fixture."""
    data = png_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset, chunks = 8, {}
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind, content = data[offset + 4 : offset + 8], data[offset + 8 : offset + 8 + length]
        checksum = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        assert zlib.crc32(kind + content) & 0xFFFFFFFF == checksum
        chunks[kind] = content
        offset += length + 12
    assert struct.unpack(">IIBBBBB", chunks[b"IHDR"]) == (16, 12, 8, 2, 0, 0, 0)
    pixels = zlib.decompress(chunks[b"IDAT"])
    assert len(pixels) == 12 * (1 + 16 * 3)
    assert pixels[:7] == bytes((0, 0, 0, 83, 11, 0, 83))
    assert pixels[-3:] == bytes((165, 187, 83))


@pytest.mark.parametrize(
    "url", ["https://127.0.0.1/mcp", "http://localhost/mcp", "http://example.com/mcp", "http://user:pass@127.0.0.1/mcp"]
)
def test_core_client_rejects_unowned_endpoint_before_connection(url):
    """A supplied URL must never redirect the readback to an external service."""
    with pytest.raises(ValueError, match="explicit HTTP loopback"):
        CoreClient(url)


def test_checksum_mismatch_does_not_launch_or_create_output(monkeypatch, tmp_path):
    """Unverified bytes cannot execute even for a version probe."""
    executable = tmp_path / "unverified-cli"
    executable.write_bytes(b"unverified bytes")

    def forbidden(*args, **kwargs):
        pytest.fail("must not execute a binary before verifying its expected digest")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        run(executable, tmp_path / "evidence", "0" * 64)
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    "reply", [{"id": 999, "result": {}}, {"id": 1, "error": {"code": -1}}, {"id": 1, "result": []}]
)
def test_core_client_rejects_failed_or_uncorrelated_wire_response(reply):
    """Use a genuine loopback response to preserve the request correlation boundary."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["id"] == 1
            data = json.dumps({"jsonrpc": "2.0", **reply}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = CoreClient(f"http://127.0.0.1:{server.server_port}/mcp")
        with pytest.raises(RuntimeError, match="mismatched id or invalid result"):
            client.rpc("tools/list", {})
        assert client.calls[0]["response"]["jsonrpc"] == "2.0"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
