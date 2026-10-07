# PhotoCraft capability coverage

This inventory is pinned to **PhotoCraft 0.2.0**, source commit
`ad863217386440ca968fccc9bfff65ba24e61142`. The adapter uses Core 0.20.41
for acceptance. A command appearing in a menu or registry is not evidence
that headless automation permits it, that every parameter is supported by this
adapter, or that its result has been verified.

The official release advertises 18 MCP tools and 748 engine command IDs.
Twelve MCP tools serve headless sessions; six are desktop-bridge-only. The
complete command inventory distinguishes upstream availability from adapter
exposure and validation. An unexposed adapter operation is not automatically
an upstream missing API.

The checked-in inventory has 778 records: 748 release engine commands, 18
release MCP tools, 10 CLI entries and two later-main MCP tools. The 39 adapter
tools reach bounded subsets of 41 engine command IDs. Of the other engine
IDs, 632 remain unexposed and 75 are blocked by release authorization or a
registered placeholder. This is an audited inventory, not full editor coverage.

Read its axes separately: `headless_status` describes source availability;
`release_authorizer_status` records the actual release authorization check;
`automation_status` adds the audit's state/parameter safety assessment;
`exposure_status` and `adapter_tools` describe the implemented facade;
`validation_status` describes the evidence for that entry. `partial` means
only a fixed typed parameter subset. `source_reviewed` does not mean that
every command or parameter has been executed. `capabilities_query` searches
this static inventory; it does not report a live document's enabled commands.

## Official MCP surface

| Official 0.2.0 tool | Adapter route or status | Boundary |
| --- | --- | --- |
| `session_list` | `document_session(action="list")` | One managed session; no desktop instance binding. |
| `doc_new` | `document_new` | Bounded RGB8 creation; upstream mode/depth variants are not all exposed. |
| `doc_open` | `document_open` | Staged PNG/JPEG; native reopen only for unchanged outputs recorded in the same session. |
| `doc_inspect` | `document_inspect` | Active-document readback; select a document explicitly first. |
| `doc_select` | `document_session(action="select")` | Validate the returned live index; no fabricated instance identity. |
| `doc_close` | `document_session(action="close")` | Explicit document close; do not replay an uncertain close. |
| `doc_save` | `document_save_as` | New `.pcraft` output; no overwrite. |
| `doc_export` | `export_image` | PNG/JPEG output; other upstream formats and PSD fidelity are not claimed. |
| `doc_render_preview` | `preview` | Bounded PNG with independent image validation. |
| `command_list` | Version-pinned capability inventory | Registry presence and `enabled` do not include workspace authorization. |
| `command_run` | Reviewed typed editor operations | Internal fixed command mappings; no arbitrary command ID/JSON tool. |
| `command_batch` | No raw batch facade | Typed multi-step operations admit only their own fixed sequence and report partial/unknown failures. |
| `ui_inspect` | Desktop bridge deferred | No headless UI state. |
| `ui_screenshot` | Desktop bridge deferred | Document preview is not a desktop screenshot. |
| `ui_pointer` | Desktop bridge deferred | Pointer input requires a bound live UI and real desktop acceptance. |
| `ui_menu_invoke` | Desktop bridge deferred | Menu invocation can mutate; bridge transport retry is unsafe for non-idempotent edits. |
| `ui_set` | Desktop bridge deferred | UI state is not the headless document model. |
| `control_call` | Raw control facade excluded | Arbitrary protocol JSON is not a typed capability implementation. |

`jobs_list` and `jobs_cancel` are absent from this release. Their presence on a
later upstream main commit does not enable them here. The later source also
contains an internal `jobs_call` helper; it is not an advertised MCP tool. Core async
jobs track adapter calls; they are not PhotoCraft jobs and cannot pre-empt an
already executing native edit.

## Official CLI surface

| CLI command | Relationship to the adapter |
| --- | --- |
| `convert` | The supported subset is typed open/export with the adapter's input/format limits. No claim of universal conversion. |
| `info` | Typed document open/inspect; no unconstrained filesystem read. |
| `run` | Typed individual edits and their readback replace arbitrary command lists. |
| `batch` | Raw action-file/directory traversal is not exposed. Orchestrate explicit typed calls over manifest-listed inputs. |
| `droplet` | Stored arbitrary action execution is not exposed. |
| `commands` | Used to audit the pinned registry, not as an authorization list. |
| `mcp` | The adapter owns official headless stdio MCP. Desktop `--bridge` is not enabled. |
| `serve` | Alternate upstream transport; the adapter continues to use the official MCP SDK. |
| `--version`, `version` | Probed before startup; incompatible versions fail before editing. |
| `--help`, `help` | Informational CLI help, not an editing capability. |

## Showcase requirements and release reality

| Requirement | Official release route | Important distinction |
| --- | --- | --- |
| Import another raster as a layer | `doc_open` plus session-owned copy/paste was verified against the official headless release | This imports a Pixel layer with transparency, not a linked/embedded smart object. `file.placeEmbedded` and `file.placeLinked` are registered but rejected by the official automation workspace. Do not open an unrestricted file-command route. |
| Local editable mask | `select.rect` and selection-based layer-mask commands; mask-targeted painting | Reveal-all/hide-all alone do not prove local mask editing. Preserve the native mask and verify it after reopening. |
| Non-stretched crop | `image.crop` | This is distinct from `image.imageSize`. Verify pixels and dimensions, not only a success flag. |
| Canvas extension/reframing | `image.canvasSize` | Anchor and extension color must be explicit; preserve the original content scale. Nontransparent extension color only fills a special transparency-locked Background raster, so arbitrary document margins cannot be promised that color. |
| Layer placement and scale | `layer.translate`, `edit.transform` | Bound the transformed content rectangle before allocating; do not use document resize to pretend a layer was transformed. Explicit translation can also move linked layers, requiring complete accounting or rejection. |
| Layer stack arrangement | `layer.moveTo` and arrange commands | Reordering is distinct from spatial translation. |

The showcase must use the real editor for the operations it claims. A rendered
or externally arranged composite cannot be presented as evidence of PhotoCraft
layer import, local masking, canvas layout, or native editability. Keep input
provenance, each meaningful edit, before/after exports, native save/reopen and
independent image checks in the artifact manifest.

The upstream five-operation probe used a small synthetic raster fixture with
colored regions and transparency, imported into a blank document. It verified imported
alpha, editable local mask changes, exact crop pixels, canvas offset without
resampling, translated/transformed markers, and native save/reopen. This proves
the upstream routes; adapter acceptance must separately exercise the new typed
tools and Core job path at the final candidate commit.

## Noise reduction and local coverage

PhotoCraft 0.2.0 implements `filter.noise.reduceNoise` and
`filter.noise.despeckle`. The typed `noise_reduce` and `noise_despeckle` routes
restrict these operations to an explicitly selected Pixel layer. They reject
Smart layers and channel/Quick Mask editing states. Reduce Noise exposes only
the release's bounded strength, detail preservation, color-noise reduction,
detail sharpening and JPEG-artifact options. Despeckle uses the release's fixed
radius 2, threshold 12 Surface Blur implementation.

A raster selection restricts the pixels written by either filter. Load an
existing editable layer mask with `selection_from_mask`, or create a bounded
selection with `selection_rect`, before filtering. The current document and
an explicit layer ID are required when loading mask coverage. A layer mask
alone does not automatically become filter coverage. Require the returned
`document.hasSelection` to be true before a regional filter: an empty mask can
return `selected=false` with no selection, in which case the filter would
process the whole layer. Stop that regional step instead. Neighborhood filtering
can sample surrounding pixels while preserving zero-coverage output pixels.
Clear the selection after the intended regional edit, not before it.

These are destructive pixel filters with undo support; they are not editable
Smart Filter stacks or a Blender AOV denoiser. Keep an original raster layer
or input and save a separate native project. Repeated filtering changes pixels
again and must not be replayed after an uncertain response. Native `median`,
`dustAndScratches` and configurable `surfaceBlur` also exist, but are recorded
as unexposed in this adapter. Deterministic real-engine pixel checks establish
behavior; visual quality on a specific rendered scene needs its own
before/after comparison and user review.

## Deliberate boundaries

- The engine's workspace authorization remains authoritative. File-bearing
  commands denied by it remain denied; raster staging is not permission to
  remove upstream checks.
- Desktop bridge transport can replay a non-idempotent request after failure.
  Desktop tools remain deferred until recovery and live UI acceptance are
  verified. No production GUI is used by headless acceptance.
- Arbitrary external native files, linked assets, plug-in code, paid cloud
  services, and unrestricted filesystem/profile operations are not covered by
  a raster-document workflow. Their existence is recorded separately from
  safe typed exposure.
- After timeout, malformed correlation, child exit or uncertain mutation,
  do not retry the mutation. Inspect available evidence and stop the poisoned
  session. A multi-step edit is not an atomic transaction unless proven.
- A green source CI or broad inventory is not release approval. Exact artifact
  acceptance, installation/rollback, compatibility/catalog requirements,
  supported Python policy and user acceptance of the showcase remain gates.

## Primary sources

- [Release and checksums](https://github.com/storytold/photocraft/releases/tag/v0.2.0)
- [Official MCP implementation](https://github.com/storytold/photocraft/blob/v0.2.0/crates/automation/src/server.rs)
- [Automation workspace authorization](https://github.com/storytold/photocraft/blob/v0.2.0/crates/automation/src/workspace.rs)
- [Control protocol](https://github.com/storytold/photocraft/blob/v0.2.0/docs/control-protocol.md)
- [Official CLI dispatch](https://github.com/storytold/photocraft/blob/v0.2.0/apps/photocraft-cli/src/lib.rs)
- [Desktop bridge recovery](https://github.com/storytold/photocraft/blob/v0.2.0/crates/automation/src/bridge.rs)

Upstream-derived command metadata is redistributed under the MIT option; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
