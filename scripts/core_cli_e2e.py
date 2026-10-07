"""Run the official CLI against an isolated Core/PhotoCraft headless service.

Install the adapter first (or set PYTHONPATH=src), then run::

    python scripts/core_cli_e2e.py --executable /path/to/photocraft-cli \
        --cli /path/to/dcc-mcp-cli --workspace /path/to/a-new-e2e-directory

The workspace must not exist. Core owns an isolated embedded gateway on an
ephemeral loopback port, with its remote listener disabled. No desktop is
started. Every tool slug comes from CLI search. Reports retain public-safe evidence;
private subprocess diagnostics stay in the user-selected workspace.
"""

import argparse
import hashlib
import json
import os
import queue
import socket
import subprocess
import sys
import threading
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

from headless_e2e import exercise, make_fixture
from jsonschema import Draft202012Validator

from dcc_mcp_photocraft import __version__


class E2EFailure(Exception):
    """Stable public-safe failure code."""


def require(condition, code):
    if not condition:
        raise E2EFailure(code)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest():
    root = Path(__file__).resolve().parent.parent
    files = sorted(
        path
        for base in (root / "src", root / "scripts")
        for path in base.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".md"}
    )
    hashes = {path.relative_to(root).as_posix(): digest(path) for path in files}
    return {
        "files": hashes,
        "sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    }


def isolated_env(workspace):
    env = os.environ.copy()
    # Prevent inherited profiles, routes, custom skills and persistence paths
    # from connecting this test to an operator's production service.
    for key in tuple(env):
        if key.startswith("DCC_MCP_"):
            del env[key]
    for name, relative in {
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
        "HOME": "home",
        "USERPROFILE": "home",
        "DCC_MCP_REGISTRY_DIR": "registry",
        "DCC_MCP_LOG_DIR": "logs",
    }.items():
        path = workspace / relative
        path.mkdir(exist_ok=True)
        env[name] = str(path)
    env.update(
        DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS="1",
        DCC_MCP_CLI_NO_AUTO_GATEWAY="true",
        DCC_MCP_GATEWAY_PROFILE="local",
        DCC_MCP_NON_INTERACTIVE="true",
        DCC_MCP_GATEWAY_HOST="127.0.0.1",
        DCC_MCP_GATEWAY_REMOTE_HOST="127.0.0.1",
        DCC_MCP_GATEWAY_REMOTE_PORT="0",
        DCC_MCP_GATEWAY_ADMIN_DB=str(workspace / "gateway-admin.sqlite"),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONUNBUFFERED="1",
    )
    # Retain source-checkout operation without depending on the child's cwd.
    if env.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.pathsep.join(
            str(Path(entry).resolve()) for entry in env["PYTHONPATH"].split(os.pathsep) if entry
        )
    return env


def worker(args):
    from dcc_mcp_core import McpHttpConfig, McpHttpServer, ToolRegistry

    from dcc_mcp_photocraft.paths import Workspace
    from dcc_mcp_photocraft.server import PhotoCraftServer

    # GatewayOptions uses zero to disable registration. Ask the OS for an
    # unused candidate, then require that this service won its own election.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        gateway_port = reservation.getsockname()[1]
    # Core 0.20.41's DccServerOptions does not forward remote-listener
    # configuration. Its public native server API does. A test-owned empty
    # controller wins this isolated gateway first; the adapter only registers.
    gateway_config = McpHttpConfig(port=0, server_name="PhotoCraft E2E controller")
    gateway_config.dcc_type = "photocraft-e2e-controller"
    gateway_config.gateway_port = gateway_port
    gateway_config.gateway_remote_host = "127.0.0.1"
    gateway_config.gateway_remote_port = 0
    gateway_config.registry_dir = os.environ["DCC_MCP_REGISTRY_DIR"]
    controller = McpHttpServer(ToolRegistry(), gateway_config)
    gateway = controller.start()
    service = None
    try:
        require(gateway.is_gateway, "isolated_gateway_ownership")
        service = PhotoCraftServer(
            args.executable,
            Workspace(args.workspace / "inputs", args.workspace / "outputs"),
            gateway_port=gateway_port,
            enable_gateway_failover=False,
            enable_telemetry=False,
            enable_job_persistence=False,
            enable_checkpoint_persistence=False,
            enable_checkpoint_tools=False,
        )
        service.start()
        require(not service.is_gateway, "adapter_registers_with_owned_controller")
        print(json.dumps({"e2e_ready": True, "instance_id": service.instance_id}), flush=True)
        require(sys.stdin.readline().strip() == "stop", "worker_stop_request")
    finally:
        try:
            if service is not None:
                service.stop()
                (args.workspace / "manifest.json").write_text(
                    json.dumps(service.facade.workspace.manifest(), indent=2), encoding="utf-8"
                )
                print(
                    json.dumps({"e2e_stopped": True, "instance_id": service.instance_id}),
                    flush=True,
                )
        finally:
            gateway.shutdown()


class CliRun:
    def __init__(self, cli, env, workspace, report):
        self.cli, self.env, self.workspace, self.report = cli, env, workspace, report
        self.slugs = {}
        self.schemas = {}

    def command(self, *arguments, payload=None, allow_failure=False):
        index = len(self.report["commands"])
        result = subprocess.run(
            [
                str(self.cli),
                "--no-auto-gateway",
                "--gateway",
                "local",
                "--output",
                "json",
                *arguments,
            ],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            cwd=self.workspace,
            timeout=90,
        )
        (self.workspace / "diagnostics" / f"cli-{index:03d}.json").write_text(
            json.dumps(
                {"arguments": arguments, "stdout": result.stdout, "stderr": result.stderr}, indent=2
            ),
            encoding="utf-8",
        )
        self.report["commands"].append({"action": arguments[0], "exit_code": result.returncode})
        require(allow_failure or result.returncode == 0, "cli_" + arguments[0] + "_failed")
        try:
            return json.loads(result.stdout)
        except ValueError:
            if allow_failure and result.returncode != 0:
                return {"_cli_exit_code": result.returncode, "_stderr": result.stderr}
            raise E2EFailure("cli_invalid_json") from None

    def check(self, name, condition, **evidence):
        self.report["checks"].append({"name": name, "passed": bool(condition), **evidence})
        require(condition, name)

    def describe(self, name):
        if name not in self.schemas:
            self.schemas[name] = self.command("describe", self.slugs[name])["tool"]
        return self.schemas[name]

    def call(self, tool, **arguments):
        description = self.describe(tool)
        validator = Draft202012Validator(description["inputSchema"])
        require(validator.is_valid(arguments), "input_schema_validation_" + tool)
        step = {"tool": tool, "arguments": arguments, "schema_validated": True, "status": "running"}
        self.report["steps"].append(step)
        options = ["--wait", "--wait-timeout-secs", "60"] if tool != "connection_status" else []
        response = self.command(
            "call", self.slugs[tool], "--json-file", "-", *options, payload=arguments
        )
        require(response.get("success") is True, "cli_call_envelope_" + tool)
        require(response.get("control_route") == "local_mcp_direct", "unexpected_control_route")
        payload = response.get("result", {}).get("structuredContent", {})
        require(response["result"].get("isError") is False, "mcp_error_" + tool)
        step["request_id"] = response.get("request_id")
        if tool != "connection_status":
            wait = response.get("wait", {})
            require(
                wait.get("terminal") is True
                and wait.get("completed") is True
                and wait.get("owner") == "core"
                and wait.get("status") == "completed"
                and wait.get("job_resubmitted") is False,
                "core_job_not_completed_" + tool,
            )
            require(
                payload.get("job_id_owner") == "core"
                and payload.get("status") == "completed"
                and payload.get("core_job_id") == wait.get("job_id")
                and payload.get("job_id") == wait.get("job_id"),
                "core_job_identity_" + tool,
            )
            step.update(
                core_job_id=payload["core_job_id"],
                terminal_status=payload["status"],
                job_resubmitted=False,
            )
            payload = payload["result"]
        require(payload.get("success") is True, "adapter_failed_" + tool)
        require(isinstance(payload.get("context"), dict), "adapter_context_" + tool)
        step["status"] = "passed"
        return payload["context"]


def run(args):
    try:
        args.workspace.mkdir(parents=True, exist_ok=False)
    except OSError:
        print(json.dumps({"status": "failed", "error": "workspace_must_be_new_and_writable"}))
        return 1
    for name in ("inputs", "outputs", "diagnostics"):
        (args.workspace / name).mkdir()
    env = isolated_env(args.workspace)
    make_fixture(args.workspace / "inputs" / "gradient.png")
    fixture_hash = digest(args.workspace / "inputs" / "gradient.png")
    report = {
        "schema_version": 1,
        "route": "official_cli_local_mcp_direct_to_core_to_photocraft_headless",
        "gateway_tested": False,
        "desktop_tested": False,
        "started_utc": datetime.now(UTC).isoformat(),
        "versions": {
            "dcc_mcp_core": version("dcc-mcp-core"),
            "adapter": __version__,
        },
        "artifacts": {
            "photocraft_executable_sha256": digest(args.executable),
            "cli_sha256": digest(args.cli),
        },
        "commands": [],
        "steps": [],
        "checks": [],
        "source": source_manifest(),
        "status": "running",
    }
    cli = CliRun(args.cli, env, args.workspace, report)
    ready = queue.Queue()
    service = None
    diagnostics = (args.workspace / "diagnostics" / "server.log").open("w", encoding="utf-8")
    try:
        service = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--executable",
                str(args.executable),
                "--cli",
                str(args.cli),
                "--workspace",
                str(args.workspace),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=diagnostics,
            env=env,
            cwd=args.workspace,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def read_output():
            for line in service.stdout:
                diagnostics.write(line)
                diagnostics.flush()
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and (
                    message.get("e2e_ready") or message.get("e2e_stopped")
                ):
                    ready.put(message)
            ready.put({"e2e_worker_exited": True})

        threading.Thread(target=read_output, daemon=True).start()
        try:
            handshake = ready.get(timeout=45)
        except queue.Empty:
            raise E2EFailure("server_readiness_timeout") from None
        require(handshake.get("e2e_ready") is True, "server_bootstrap_failed")
        instance_id = handshake.get("instance_id")
        cli.check("canonical_instance_id", isinstance(instance_id, str) and len(instance_id) == 36)
        inventory = cli.command("list")
        selected = [row for row in inventory["instances"] if row["instance_id"] == instance_id]
        cli.check(
            "owned_standalone_instance_discovered",
            len(selected) == 1
            and selected[0]["pid"] == service.pid
            and selected[0]["dcc_type"] == "photocraft"
            and selected[0]["version"] == "0.2.0"
            and selected[0]["host"] == "127.0.0.1"
            and selected[0]["metadata"]["dcc_mcp_instance_type"] == "standalone",
        )
        cli.check(
            "only_isolated_instances_visible",
            inventory["total"] == 2
            and {row["dcc_type"] for row in inventory["instances"]}
            == {"photocraft", "photocraft-e2e-controller"},
        )
        (args.workspace / "diagnostics" / "inventory.json").write_text(
            json.dumps(inventory, indent=2), encoding="utf-8"
        )
        search = cli.command(
            "search",
            "--query",
            "PhotoCraft document",
            "--dcc-type",
            "photocraft",
            "--instance-id",
            instance_id,
            "--limit",
            "30",
        )
        (args.workspace / "diagnostics" / "search.json").write_text(
            json.dumps(search, indent=2), encoding="utf-8"
        )
        hits = search.get("hits", [])
        candidate = next(
            (hit for hit in hits if hit.get("skill_name") == "photocraft-document"), None
        )
        cli.check(
            "unloaded_skill_discovered",
            candidate is not None and candidate["next_step"]["action"] == "load_skill",
        )
        load = cli.command("load-skill", "--json", json.dumps(candidate["next_step"]["arguments"]))
        cli.check(
            "typed_skill_loaded",
            load.get("loaded") is True
            and load.get("partial") is False
            and load.get("tool_count") == 15,
        )
        loaded = cli.command(
            "search", "--dcc-type", "photocraft", "--instance-id", instance_id, "--limit", "100"
        )
        (args.workspace / "diagnostics" / "loaded.json").write_text(
            json.dumps(loaded, indent=2), encoding="utf-8"
        )
        cli.slugs = {
            hit["backend_tool"]: hit["slug"] for hit in loaded["hits"] if hit.get("kind") == "tool"
        }
        from dcc_mcp_photocraft.facade import PhotoCraftFacade

        cli.check(
            "all_typed_tools_discovered",
            set(PhotoCraftFacade.TOOL_NAMES) <= cli.slugs.keys(),
            tool_count=len(PhotoCraftFacade.TOOL_NAMES),
        )
        exercise(
            cli,
            SimpleNamespace(
                input_root=args.workspace / "inputs", output_root=args.workspace / "outputs"
            ),
        )
        cli.check(
            "all_typed_tools_called",
            set(PhotoCraftFacade.TOOL_NAMES) == {step["tool"] for step in report["steps"]},
        )
        invalid = cli.command(
            "call",
            cli.slugs["document_new"],
            "--json-file",
            "-",
            "--wait",
            payload={"width": 0, "height": 16},
            allow_failure=True,
        )
        invalid_job = invalid.get("result", {}).get("structuredContent", {})
        cli.check(
            "core_rejects_invalid_dimensions",
            invalid.get("success") is False
            and invalid.get("wait", {}).get("terminal") is True
            and invalid_job.get("job_id_owner") == "core"
            and invalid_job.get("status") == "failed"
            and "validation failed: params.width" in invalid_job.get("error", ""),
            core_job_id=invalid_job.get("core_job_id"),
        )
        invalid_path = cli.command(
            "call",
            cli.slugs["document_open"],
            "--json-file",
            "-",
            "--wait",
            payload={"path": "../outside.png"},
            allow_failure=True,
        )
        path_job = invalid_path.get("result", {}).get("structuredContent", {})
        path_result = path_job.get("result", {})
        cli.check(
            "adapter_rejects_path_traversal",
            invalid_path.get("wait", {}).get("terminal") is True
            and path_job.get("job_id_owner") == "core"
            and path_job.get("status") == "completed"
            and path_result.get("success") is False
            and path_result.get("error") == "invalid_path",
            core_job_id=path_job.get("core_job_id"),
            adapter_error=path_result.get("error"),
        )
        session = cli.call("document_session", action="list")["session"]
        cli.check("rejected_calls_do_not_create_documents", session["documents"] == [])
        cli.check(
            "input_sha256_unchanged",
            digest(args.workspace / "inputs" / "gradient.png") == fixture_hash,
        )
        report["status"] = "passed"
    except (E2EFailure, subprocess.TimeoutExpired) as error:
        report["status"] = "failed"
        report["error"] = str(error) if isinstance(error, E2EFailure) else "subprocess_timeout"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = "unexpected_" + type(error).__name__
    finally:
        if service is not None:
            if service.poll() is None:
                try:
                    service.stdin.write("stop\n")
                    service.stdin.flush()
                except (BrokenPipeError, OSError):
                    report["status"] = "failed"
                    report["cleanup_error"] = "worker_stop_pipe_closed"
                try:
                    service.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    service.kill()
                    service.wait(timeout=10)
            report["service_exit_code"] = service.returncode
            try:
                remaining = cli.command("list")
                report["post_stop_inventory"] = {"total": remaining.get("total")}
                cli.check("registry_cleaned_after_stop", remaining.get("total") == 0)
                cli.check("worker_clean_exit", service.returncode == 0)
            except E2EFailure as error:
                report["cleanup_error"] = str(error)
                report["status"] = "failed"
        diagnostics.close()
        report["completed_utc"] = datetime.now(UTC).isoformat()
        report["tool_call_count"] = sum(
            command["action"] == "call" for command in report["commands"]
        )
        if source_manifest()["sha256"] != report["source"]["sha256"]:
            report["status"] = "failed"
            report["error"] = "source_changed_during_run"
        (args.workspace / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "error": report.get("error"),
                "checks": len(report["checks"]),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.executable, args.cli, args.workspace = (
        p.resolve() for p in (args.executable, args.cli, args.workspace)
    )
    if args.worker:
        worker(args)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
