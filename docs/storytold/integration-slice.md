# First Storytold integration slice

## Ownership and scope

Reuse `dcc-mcp/dcc-mcp-photocraft` rather than making a second adapter. Existing
`dcc-mcp-filmcraft` and `dcc-mcp-pdfcraft` own their products. The official
`storytold/craft-launcher` installs, verifies, updates and opens the Craft apps;
this adapter only reads its install hints. The former `loonghao/dcc-mcp`
launcher is retired. Core CLI and Admin own unified discovery and lifecycle.

The [pinned matrix](products.md) covers ArtCraft, thirteen Craft applications
and the Launcher. The full organization inventory has 68 repositories, not 68
adapter targets. Most products explicitly remain alpha or pre-alpha. PhotoCraft
is selected for its released scoped local interfaces and deterministic image
readback, not a claim of production maturity. Spoken WinCraft is unresolved;
LightCraft is confirmed. Preserve PdfCraft/PrintCraft and DeckCraft/SlideCraft
as observed naming differences rather than duplicate adapters.

## Delivered implementation

- Discover the official Launcher's `installed.json` PhotoCraft record and native
  portable/tarball layouts, with bounded reads and absolute paths. Follow only
  known native executable names and macOS bundle layouts. Do not unpack or run
  AppImages, invoke package managers, change the manifest or start a GUI.
- Keep configured PhotoCraft paths and PATH ahead of launcher hints. For a
  portable launcher, set `PHOTOCRAFT_LAUNCHER_DATA` to the data directory that
  actually contains `installed.json` (often `ArtCraftLauncherData/data`).
- Use Core's public `instance_type="standalone"` and remote-listener options.
  The service has independent lifetime and no bound GUI PID. Remote gateway
  listening defaults to disabled; an explicitly configured operator can change
  it without adapter-owned daemon or transport code.
- Require the native MCP protocol, `photocraft` identity and stable version
  >=0.2.0 before tool discovery. Validate live tool schemas and reviewed engine
  parameters separately. Self-reported identity is not an executable signature.
- Redact inline control tokens from the complete doctor output, including its
  environment section. No credentials or tokens are created for this slice.
- Keep the typed Skill's real native save/reopen/export workflow. An acceptance
  receipt records artifact hashes, actual color metadata and pixel readback for
  subsequent showcase consumers. It is test evidence, not another Core asset
  protocol or an assertion that those consumers have imported the asset.

Launcher source: commit
[`c0fa1c69f452bab61ec3fd806646ef4daff9912a`](https://github.com/storytold/craft-launcher/tree/c0fa1c69f452bab61ec3fd806646ef4daff9912a),
`crates/native/src/paths.rs`, `manifest.rs`, `install.rs` and `detect.rs`.
No upstream binaries, logos, icons, fonts or media are shipped by this adapter.

## Runtime and installation gates

PhotoCraft's official Rust CLI/MCP needs no embedded Python. This existing
Python adapter uses Core for registry, dispatch and Skills. When packaging its
external Python service, prefer the shared DCC-MCP Runtime under the canonical
[Creator runtime rule draft](https://github.com/dcc-mcp/dcc-mcp-agent-plugins/pull/39).
Do not provision a Python interpreter per Craft application or replace official
JS/native interfaces with Python.

Runtime source `3a54f666c4283bab160dbab3ac90630c9ed39087` exposes manifest,
bootstrap, handshake and planning APIs; its CLI is limited and its example
payload hashes are placeholders. Its existing cp311/Core requirement checks
must agree with this adapter's dependencies. No compatible verified payload
installation has been established here. Runtime integration remains a gate,
not a successful install claim. Core
[#2252](https://github.com/dcc-mcp/dcc-mcp-core/issues/2252) and the runtime owner
retain that shared work. This change neither updates shared runtimes nor
restarts a daemon. No catalog install entry should be added without the exact
released wheel URL/hash and a validated install/rollback round trip.

## Exact native candidate and evidence

Official PhotoCraft [v0.15.1](https://github.com/storytold/photocraft/releases/tag/v0.15.1),
release commit `60c87b9884fdae0e464614d482a3bc435cbbc578`:

| Artifact | SHA-256 |
|---|---|
| Windows x64 portable ZIP | `f41bc0fc1c6d40377251bfc4a2ecd60014d1d9aca07e5101608e5e90f5ac1fd2` |
| Windows x64 CLI inside that ZIP | `a4ed7673232cb9085caca5ed0433878dcce3828eeae97f04d492eaec1948b511` |
| Linux x86_64 tar.gz | `b622be40355513056f8152afe895d0a05c1bc3c344a027f772a9a12fa3d1c223` |
| Linux x86_64 CLI inside that tar.gz | `16fbb244d69d5c32dd60435e95b1c893d04bf7ef6e3d37fb967eecb57f6e0bea` |

Both archives matched official release API digests and `SHA256SUMS.txt` before
use. Archive verification identifies these candidates; it is not a blanket
trust decision for unrelated software or all upstream components.

`scripts/native_readback.py` checks the configured CLI hash before execution,
then uses genuine Core HTTP MCP and the packaged Skill for commands, methods
and inspection of an original 16x12 PNG. It grants no write root, GUI access or
gateway. Windows v0.15.1/Core 0.20.44 passed its fourteen readback, integrity and
cleanup checks on 2026-10-24. The executable, input and source bytes remained
unchanged; the listener and dispatch thread closed. This does not independently
sweep native process IDs or qualify image editing.

The `Official Linux native image workflow` CI job verifies this Linux candidate
and runs `scripts/native_showcase.py`: original PNGs, embedded layer/mask/Curves
editing, native save/close/reopen, further edits, PNG export, independent pixel
and native-container checks, rejection paths and child/listener cleanup. The
earlier v0.8.2 acceptance remains documented in
[native-image-workflow.md](../native-image-workflow.md). A new CI job is only
qualified after its report passes; inspect that run instead of treating its
configuration as evidence of success.

## Showcase handoff and remaining host gates

The first asset route is PhotoCraft PNG + native project -> separately accepted
FilmCraft media import or CAD/DCC material/render input -> separately accepted
Nuke read/composite. The official editor name is **FilmCraft**. Hashes identify
original and edited bytes; PNG dimensions, RGB/RGBA depth, alpha, observed color
chunks and decoded pixels establish the actual handoff boundary. Untagged RGB
fixtures are sRGB-intended synthetic values: consumers must explicitly assign
sRGB, and no OCIO, scene-linear or EXR conversion has been tested. `.pcraft` is
kept for PhotoCraft editing and is not a Photoshop interchange guarantee.

No Nuke process, existing desktop or shared example file was opened or changed.
The Nuke owner retains host control; consumer readback and render comparison
remain pending. Full Windows editing remains unavailable because secure
descriptor-relative no-follow publication is absent. GUI control has no grant
in the current serial desktop schedule. A desktop test needs a new exact owned
instance, a confirmed time window, the official authenticated control channel,
and before/after state. A headless result cannot satisfy that gate.

Subsequent batches can reuse official CLI/MCP/JS capabilities in VectorCraft,
FilmCraft and the other licensed products, respecting each product's flags,
authentication, schema and recovery rules. ArtCraft's cloud/API path requires
separate auth, service cost and sensitive-data authorization; it is not assumed
to expose the same local stdio server. Review each product's code, brand,
fonts/media and service licenses independently; the MIT/Apache code grants do
not license all bundled components.
