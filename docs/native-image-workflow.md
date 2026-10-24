# Native image workflow

## What is added

One focused `execute_photocraft_image_batch` Skill tool preserves the existing
Core adapter architecture and its five discovery/document/UI tools. Each batch
owns one fresh official headless `photocraft-cli mcp` session. Fixed Skill entry
scripts bind action identity; constructor binary/read/write configuration is
scoped to the current server instance. The startup/readiness contract is tested
against Core 0.20.44. Existing catalog calls use the actual one-shot `commands`
shape without invalid root flags. Existing document inspection uses a scoped
`serve` open/inspect/close batch, with every nested result checked, rather than
unscoped one-shot file access. External UI socket clients run with `any` Python thread
affinity; they are not embedded Python executing inside PhotoCraft's UI thread.

The 11 typed step kinds intentionally do not restore the entire historical
39-tool implementation or its old host/schema inventory. Discovery is live,
while operation choice and parameter bounds are a reviewed adapter policy.
MCP tool arguments are checked against current native JSON Schemas. Engine
command arguments have no upstream JSON Schemas, so they receive strong local
validation plus checks of live command existence and documented parameters.

## Ownership and preservation

- Input PNG bytes are copied unchanged from the configured read authority.
- Only one document is open at a time. Callers use symbolic document/layer aliases,
  never ambient session indices or arbitrary layer IDs.
- Placement is an embedded editable Smart Object; masks and Curves stay editable.
- Mask creation reads the owned document back before clearing its selection.
  Newer PhotoCraft releases consume that selection while creating the mask;
  older releases retain it. Deselect only a still-present selection, then
  verify the owned mask remains and the selection is absent.
- Native revision values can reset and session indices renumber after close.
- Curves readback accepts only the reviewed internal `Curves` enum or the newer
  `kind: curves` command-parameter summary, checking requested master points and
  identity color channels. Unknown schemas or changed curve values are rejected;
  the saved native project retains its original format.
  Reopen is limited to an unchanged native save from this same batch. Saved layer
  signatures are verified before rebinding aliases to reopened IDs.
- Native save/export can overwrite files upstream. This adapter instead reserves
  new outputs, stages native writes, verifies regular bounded files and publishes
  with no-follow/no-overwrite operations. Read and write roots remain distinct
  user-granted authorities; the native child receives only its private stage.
- No arbitrary native project imports, generic commands/control calls, scripts,
  public gateway, credential creation or GUI automation are added.
- These controls are adapter boundaries, not an operating-system sandbox or hard
  disk quota. The configured executable is trusted launcher configuration.

## Failure semantics

A batch is not atomic. Any dispatched edit may already have applied when a host
error or caller timeout occurs. Never automatically replay. The owned process is
bounded and closed; unsaved state does not carry into another call. Failure
reports identify completed steps and any already-published outputs. New batches
may be used for independent recovery only after checking those outputs. Caller
cancellation is not a native rollback or a promise that later phases stopped.

## Runtime provenance

Actual acceptance uses official Linux x86_64 v0.8.2, commit
`d43db3e153c76aefce9b32cef9099e458eaf17a8`:

- [Official release](https://github.com/storytold/photocraft/releases/tag/v0.8.2)
- [Linux archive](https://github.com/storytold/photocraft/releases/download/v0.8.2/photocraft-0.8.2-linux-x86_64.tar.gz)
- Archive SHA-256: `16c3089976eee2d290dbe73986db4835c1ec5a043c5c16b017b24848ff5ea827`
- CLI SHA-256: `f0140a060462d35fcc0ce888cbf7ab05cd7dfbb52da96f8472f207f00cb3b2eb`

Archive bytes matched both official release API digest and SHA256SUMS before
execution. Runtime binaries are not redistributed. Default unit tests do not
require the host; separate real-host acceptance must not be confused with fake
protocol tests or upstream source inspection. Other versions/platforms and the
desktop GUI are not automatically qualified by this Linux native result.

The release Web/WASM shell lacks a reviewed exported editing-command dispatcher.
This work neither adds an upstream browser overlay nor substitutes a native
session for a browser document.

## Qualified acceptance (2026-10-14)

- Genuine MCP SDK 1.30 → Core 0.20.44 HTTP → official native 0.8.2:
  **394 checks passed**, 48 Core tool calls exercising all six fixed wrappers.
- One 42-step workflow imports original PNGs, places an embedded Smart Object,
  edits its mask and a Curves adjustment, saves/closes/reopens, continues editing,
  and exports verified pixels. All 17 successful image batches observed exactly
  one owned native MCP process. Four legacy discovery/read calls bring the
  audited native invocation count to 21.
- Fourteen rejected-input/authority cases each recover through a fresh valid
  batch; source/input/runtime bytes are unchanged. All four test listeners,
  registries, native processes and private stages are closed/cleaned afterward.
- Independent Pillow comparison verifies pixels, alpha preservation and exact
  native save/reopen restoration. Independent native-container extraction also
  confirms the embedded Smart Object retains the original PNG bytes.
- 148 unit/protocol tests pass locally; Ruff, formatting, Install SOP validation,
  and independent source/evidence review pass with no remaining P1/P2 findings.
  Python 3.9 syntax was checked locally; cross-platform runtime qualification is
  delegated to the existing CI matrix, not inferred from this Linux run.
- Evidence report SHA-256:
  `a26e92e5c230ee66489ba0e3e009b802bcbaedb07cd2028302ce231009ee79b8`.
  The separate real-host CI job recreates evidence and uploads it as
  `photocraft-native-core-evidence`; local evidence is not committed.

Reproduce with optional `mcp` and development extras plus Pillow installed:

```sh
python scripts/native_showcase.py --cli /absolute/path/to/photocraft-cli \
  --output evidence/native-core-local
```

The harness creates its own original geometric fixtures by default, as CI does.
The local report hash above instead uses the separately supplied Solar Geometry
poster inputs via `--input-dir /path/to/originals` (containing
`original-landscape.png` and `original-sun-field.png`). Both run the same
workflow/oracles; their visuals and artifact hashes differ. Output must be a new
directory; no user documents or credentials are required.

## Explicit limits

The accepted input subset is non-interlaced RGB/RGBA 8-bit PNG, with only fixed-
size sRGB/gAMA/cHRM/pHYs ancillary metadata; arbitrary/compressed metadata is
rejected. Limits are 48 steps, 16 layers, 4096 pixels per side, 4,194,304 pixels
per image, 16,777,216 aggregate layer pixels, 32 MiB per input and 128 MiB total
inputs, 128 MiB per output and 256 MiB total outputs. Native execution is bounded
to 120 seconds. Worst-case output capacity is reserved before each native write,
then actual byte totals are checked before publication. These are application
budgets, not OS-enforced resource limits.

Secure publication requires supported `dir_fd` and `O_NOFOLLOW` operations;
platforms without these capabilities fail closed. A published artifact is marked
`verified: true` only after identity/readback checks. A publication race can
leave an explicitly reported `verified: false` partial artifact without an
asserted stale digest. Review failure outputs before taking further action.
