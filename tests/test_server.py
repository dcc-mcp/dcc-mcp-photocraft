"""Real Core construction/discovery; this test does not launch PhotoCraft."""

import sys

from dcc_mcp_core import validate_skill

from dcc_mcp_photocraft.paths import Workspace
from dcc_mcp_photocraft.server import PhotoCraftServer


def test_core_discovers_bundled_skill_without_starting_host(tmp_path, monkeypatch):
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DCC_MCP_CHECKPOINT_IN_MEMORY", "1")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    server = PhotoCraftServer(
        sys.executable,
        Workspace(inputs, tmp_path / "outputs"),
        registry_dir=str(tmp_path / "registry"),
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_checkpoint_persistence=False,
        enable_checkpoint_tools=False,
        enable_telemetry=False,
        enable_gateway_failover=False,
    )
    try:
        assert server.photocraft.status()["state"] == "new"
        assert server.instance_id is None
        assert server.get_skill("photocraft-document") is not None
        from pathlib import Path

        skill = Path(__file__).parents[1] / "src/dcc_mcp_photocraft/skills/photocraft-document"
        assert validate_skill(str(skill)).is_clean
    finally:
        server.stop()
