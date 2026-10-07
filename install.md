<!-- install-sop-version: 1 -->
# Experimental source installation

No published wheel, managed Install SOP implementation or catalog install
entry is available yet. This document describes the explicit source validation
path; it does not report the release Install SOP gate as passed.

Use Python 3.11 or newer in a dedicated virtual environment. Install this
source with `python -m pip install -e '.[test]'`. It requires Core
`>=0.20.41,<0.21` and pins the official MCP client to `1.27.0`. Obtain
PhotoCraft 0.2.0 only from its [official release](https://github.com/storytold/photocraft/releases/tag/v0.2.0),
verify the published SHA-256, and keep the application outside this repository.
Do not use a desktop bridge executable or accept a different version as equivalent.

Create a read-only-to-the-adapter input directory containing PNG/JPEG assets
and a separate empty output directory. Start the foreground service:

```sh
dcc-mcp-photocraft --executable /path/to/photocraft-cli --input-root /work/inputs --output-root /work/outputs
```

The port defaults to an OS-assigned loopback port. The printed `mcp_url` is the
direct endpoint. Gateway auto-start is disabled by default (`gateway_port=0`),
so `instance_id` is null and this foreground service does not appear in gateway
inventory. Use the CLI's direct endpoint mode to discover, load and describe
`photocraft-document`, then validate arguments before calling. For registered
gateway acceptance, the separate Core/CLI smoke starts an explicitly scoped
Core controller and joins it through the server's public `gateway_port` option.
Do not enable embedded gateway auto-start: Core 0.20.41 can also bind a remote
listener, despite the adapter's loopback endpoint configuration. Preserve the
exact returned tool slug and instance; do not invent one for an unregistered host.
Operations other than status return Core async jobs. Query the existing job
until terminal instead of resubmitting the edit.

For hermetic validation, set `DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS=1`,
`DCC_MCP_REGISTRY_DIR`, and `DCC_MCP_LOG_DIR` to dedicated candidate directories.
Keep test environment changes scoped to the launched process. On Windows,
setting that process's `APPDATA` to an isolated candidate folder also isolates
the CLI updater staging area. Never alter global credentials, proxies or DCC
desktop startup hooks for this experiment.

Run `scripts/headless_e2e.py` against the official executable, then the Core/CLI
smoke. Keep source/mock tests, real software results, CI artifacts and published
release results distinct. Ctrl-C stops the foreground service and owned child.
Remove only the dedicated environment and candidate workspace to uninstall
this manual source experiment; no desktop integration was installed.

Before any release, implement and validate Core-owned Install SOP reports and
the full plan→execute→verify→status→uninstall round trip, upgrade rollback,
native supported-Python gates, exact artifact acceptance, catalog metadata and
compatibility documentation. Do not label these deferred steps successful.
