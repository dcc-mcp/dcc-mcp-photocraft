# DCC-MCP PhotoCraft — experimental

A small typed adapter for the official **PhotoCraft 0.2.0 headless engine**.
It uses DCC-MCP Core for discovery, skill loading, HTTP/MCP, jobs and registry
lifetime, and the official Python MCP client to own one PhotoCraft stdio
process. It does not bundle PhotoCraft or start a desktop window. The default
foreground service disables gateway auto-start and exposes its direct loopback
MCP endpoint; it is not registered in gateway inventory by default.

This is an experimental source candidate, not a software release. Follow the
[installation and validation runbook](install.md).

## Supported surface

The `photocraft-document` skill contains 15 tools:

| Tools | Purpose |
| --- | --- |
| `connection_status`, `document_session` | Version/capabilities, artifact manifest; list/select/close documents |
| `document_new`, `document_open`, `document_inspect`, `document_save_as` | RGB8 documents, staged PNG/JPEG input, native save/reopen and readback |
| `layer_create`, `layer_set`, `mask_set` | Pixel layers, name/opacity/visibility and simple masks |
| `adjustment_create`, `text_create`, `document_resize` | Editable hue/saturation or vibrance, type layers, image size |
| `preview`, `export_image`, `undo` | Independently checked PNG previews, PNG/JPEG exports and edit undo |

Native `.pcraft` outputs retain editable layers. Arbitrary external `.pcraft`
inputs are deliberately deferred: the upstream native reader has a much larger
decompression budget than this adapter. Reopening is allowed only for native
outputs created by the current session whose SHA-256 still matches. PSD
round-trip compatibility is not claimed.

There is no raw `command_run`, arbitrary JSON engine command, script execution,
desktop bridge, UI automation, cloud service or upstream job/cancel facade.
Core async jobs wrap indivisible PhotoCraft operations; they are not PhotoCraft
jobs. Start once and query the returned Core job. Cancellation can stop work
before admission, but cannot pre-empt a native edit already executing.
The declarative tools use `affinity: any` with `requires_in_process: true`:
Python only dispatches typed external MCP calls. The official engine process
serializes document edits; there is no Python/GUI main-thread API to pump.

## Ownership and recovery

Each service is `instance_type=standalone` with no bound GUI PID. Its official
MCP subprocess is serialized. The adapter validates the CLI version,
`initialize` identity/capabilities and the exact release tool schemas before
accepting edits. The SDK owns JSON-RPC framing and correlation. Core job IDs
are forwarded as MCP metadata; the SDK owns the inner wire request IDs.

Core 0.20.41 can start a remote listener when its embedded gateway auto-starts.
This adapter therefore defaults `gateway_port=0` explicitly. For gateway
acceptance, the Core/CLI smoke owns a separate, explicitly loopback-only Core
controller and registers the adapter against that existing controller. The
public Core configuration limitation must be resolved before enabling automatic
gateway startup in this product's CLI.

Timeout, mismatched/missing responses and child exit poison that session.
No reconnect or mutation replay occurs. The SDK closes stdin and terminates
the managed child on shutdown. A failure after an admitted mutation reports
`indeterminate=true`; a successful native call followed by failed readback is
also indeterminate. Readback is completed even if cancellation arrives after
the edit. Do not rerun an uncertain operation automatically.

The operator supplies disjoint input and initially empty output directories.
PNG/JPEG inputs are validated and copied by digest into the output directory's
private `.imports` area. The upstream process receives read/write authority
only for this isolated output directory, never the original input root.
Public tool paths cannot address hidden staging files. Output targets must not
exist, and new subdirectories must be created by the operator. Workspace roots
must be operator-owned and not concurrently modified by another writer; these
path checks are not an OS sandbox against a hostile local process.

Limits include 64 MiB input/artifact bytes, 16 megapixels, four documents,
64 layers, 2048-pixel previews, 256 text characters, 128-point type and a
conservative text rectangle budget using document DPI. The trusted native
process is not assigned an OS memory quota. Preserve returned warnings and
the manifest from `connection_status`; the smoke scripts also save a manifest.
The service does not yet restore unsaved state or manifests across restarts.

## Validation

```sh
python -m pip install -e '.[test]'
python -m pytest
python -m ruff check src scripts tests
python -m ruff format --check src scripts tests
dcc-mcp-cli lint src/dcc_mcp_photocraft/skills/photocraft-document
python scripts/headless_e2e.py --executable /path/to/photocraft-cli --workspace /path/to/fresh-evidence
python scripts/core_cli_e2e.py --executable /path/to/photocraft-cli --cli /path/to/dcc-mcp-cli --workspace /path/to/fresh-cli-evidence
```

The live smoke uses deterministic generated input and records 15-tool editing,
input checksums, native save/close/reopen, preserved layer structure and exact
rendered pixels after reopen. Contract tests use a fake MCP process to exercise
faults and cleanup; they do not substitute for real software acceptance.

The Core/CLI smoke runs those editing checks through official CLI discovery,
skill loading, described-schema validation and Core job completion. Its private
Core controller disables the remote listener; CLI calls use local MCP directly.
It records parameter/path rejection and registry cleanup, but does not claim
gateway-routed statistics or desktop acceptance.

CI validates source, schemas and fake-process behavior on Windows and Linux.
A software release additionally requires exact artifact live acceptance,
Core/CLI discovery→describe→validate→call→readback, complete Install SOP
lifecycle and rollback coverage, catalog/compatibility updates, and review.
Python 3.11+ is the honest standalone SDK profile for this experiment. Core's
native Python 3.7 LTS gate is not satisfied by this adapter, and no compatibility
claim or release bypass is made.

## Upstream and licensing

Pinned upstream: [PhotoCraft v0.2.0](https://github.com/storytold/photocraft/releases/tag/v0.2.0),
commit `ad863217386440ca968fccc9bfff65ba24e61142`.
The code is dual licensed under
[MIT](https://github.com/storytold/photocraft/blob/v0.2.0/LICENSE-MIT) or
[Apache-2.0](https://github.com/storytold/photocraft/blob/v0.2.0/LICENSE-APACHE).
Its [brand assets have separate restrictions](https://github.com/storytold/photocraft/blob/v0.2.0/docs/brand/LICENSE-brand.txt).
This adapter ships no vendor binary, logo, icon, font or brand artwork.
The pinned tool-schema snapshot is redistributed under its MIT option; see
[third-party notices](THIRD_PARTY_NOTICES.md) for the complete upstream notice.
Names identify compatibility; no endorsement is implied. ArtCraft and its
services are outside this repository's dependency and license scope.

The [upstream control protocol](https://github.com/storytold/photocraft/blob/v0.2.0/docs/control-protocol.md)
describes headless versus desktop bridge modes. The 0.2.0 bridge retries a failed
transport request and can replay a non-idempotent edit; desktop acceptance is
deferred until that recovery boundary is addressed and tested.
