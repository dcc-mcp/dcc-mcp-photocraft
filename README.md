# dcc-mcp-photocraft

[![PyPI version](https://img.shields.io/pypi/v/dcc-mcp-photocraft.svg)](https://pypi.org/project/dcc-mcp-photocraft/)
[![Python versions](https://img.shields.io/pypi/pyversions/dcc-mcp-photocraft.svg)](https://pypi.org/project/dcc-mcp-photocraft/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

PhotoCraft adapter for the [DCC-MCP](https://github.com/dcc-mcp/dcc-mcp-core) ecosystem: lets AI agents inspect and drive [PhotoCraft](https://github.com/storytold/photocraft), the open-source image editor.

## Why this adapter exists when PhotoCraft already ships an MCP server

**PhotoCraft includes its own MCP server** (`photocraft-cli mcp`), and its documentation shows how to register it directly with an agent. If that is all you need, use it — this package does not replace it.

This adapter adds the things a standalone MCP server does not provide:

- **One control plane across every DCC.** PhotoCraft becomes one entry in the same tool registry as Maya, Blender, Houdini, Nuke and the rest, so an agent discovers and calls it through the same `dcc-mcp-cli` commands and the same gateway instead of managing a second set of per-app connections.
- **Cross-DCC orchestration.** A workflow can move from PhotoCraft to another host in one session — for example, compositing an edit here and exporting the result elsewhere.
- **Sidecar lifecycle and discovery.** The adapter registers the running instance, reports readiness, and exposes a doctor that distinguishes "PhotoCraft is not installed" from "PhotoCraft is installed but misconfigured".
- **Host detection without Rust.** It finds the `photocraft-cli` binary whether it arrived inside a GUI package or as a standalone archive, on Windows, macOS and Linux.

What it deliberately does **not** do is reimplement PhotoCraft's command set. The installed command table is discovered at runtime. Newly reported commands become visible to discovery without an adapter release; the bounded image workflow exposes only its reviewed typed operations.

## Requirements

- Python 3.9 or newer
- [PhotoCraft](https://github.com/storytold/photocraft) 0.2 or newer — only `photocraft-cli` is required; the desktop app is optional

## Installation

```bash
pip install dcc-mcp-photocraft
```

Verify the host is reachable:

```bash
dcc-mcp-photocraft-install
```

## Usage

Run the adapter:

```bash
dcc-mcp-photocraft
```

Report the detected host:

```bash
dcc-mcp-photocraft status
```

Ask your agent to inspect a document:

```text
Use dcc-mcp to summarize poster.pcraft.
```

### Configuration

| Variable | Purpose |
| --- | --- |
| `PHOTOCRAFT_CLI` | Path to `photocraft-cli` when it is not on `PATH` |
| `PHOTOCRAFT_APP` | Path to the desktop app binary when it is not on `PATH` |
| `PHOTOCRAFT_CONTROL_PORT` | The port passed to `photocraft --control <port>`; enables UI actions |
| `PHOTOCRAFT_CONTROL_TOKEN` | The control-channel bearer token |
| `PHOTOCRAFT_CONTROL_TOKEN_FILE` | Path to a file holding the token; preferred, since it keeps the credential off the process command line |
| `PHOTOCRAFT_READ_ROOT` | Read authority forwarded to `photocraft-cli --automation-read-root` |
| `PHOTOCRAFT_WRITE_ROOT` | Write authority forwarded to `photocraft-cli --automation-write-root` |

### Headless and live-UI paths

Most actions run through `photocraft-cli` and need no window, which is what makes them work on a headless server and in CI. The UI actions (`ui_screenshot`, `ui_inspect`) additionally need the desktop app started with:

```bash
photocraft --control 7878 --control-token-file /private/path/photocraft-control.token \
  --automation-read-root /work/project --automation-write-root /work/project
```

Then point `PHOTOCRAFT_CONTROL_PORT` at `7878` and supply the token through `PHOTOCRAFT_CONTROL_TOKEN_FILE`.

Two things about this channel are easy to get wrong:

- **A token is always required.** Upstream makes an `auth` frame the mandatory first request on every connection and refuses to dispatch anything before it succeeds. The port alone is not enough. If you did not pass a token at launch, upstream generated one and wrote it to standard error.
- **The port is yours to choose, and it is configured, not discovered.** Unlike the sibling PdfCraft adapter, PhotoCraft does not write its port to a file — it binds the port you give it. Because every Craft app listens on loopback, pick one that does not collide with the others.

Read and write authority are independent: a read fails without a read root and a write fails without a write root, so grant each one you need at launch.

## Host maturity

PhotoCraft is **pre-1.0 software** and upstream describes it as early alpha. Expect the command table to keep changing; discover it at runtime rather than relying on a fixed list.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
ruff check src tests
ruff format --check src tests
```

## License

MIT — see [LICENSE](LICENSE).

## Bounded native image editing

The `photocraft-core` Skill now also exposes `execute_photocraft_image_batch`.
One call owns one fresh headless native MCP process for a typed sequence of:

- `open_png`, `place_png` as an embedded editable Smart Object
- `set_layer`, `mask_rectangle`, `set_mask`, non-destructive `curves`
- `inspect`, `save_project`, `close`, `reopen_project`, `export_png`

The existing five discovery/document/UI tools are retained. This workflow uses
reviewed typed translations, not arbitrary commands or a copy of the complete
upstream catalog. Required command availability and native MCP tool schemas are
checked against the installed host. Engine command parameter documentation is
prose; those arguments use adapter-owned typed checks, not purported live JSON
Schema validation. A new upstream command is not automatically exposed by this
bounded editing tool.

Set `PHOTOCRAFT_CLI`, `PHOTOCRAFT_READ_ROOT` and `PHOTOCRAFT_WRITE_ROOT` before
starting the adapter. Read and write roots may be different. All workflow file
names are relative to their applicable root. The adapter stages original PNGs
without changing them and gives its owned native process access only to its own
staging directory. Output publication never replaces an existing destination.

```json
{
  "steps": [
    {"op":"open_png","document":"poster","input":"original.png","base_layer":"base"},
    {"op":"curves","document":"poster","layer":"tone","points":[[0,0],[128,160],[255,255]]},
    {"op":"save_project","document":"poster","output":"poster.pcraft"},
    {"op":"close","document":"poster"},
    {"op":"reopen_project","document":"poster","output":"poster.pcraft"},
    {"op":"export_png","document":"poster","output":"poster.png"}
  ]
}
```

A batch is not an atomic transaction. On failure or timeout, completed native
steps may have applied; never automatically replay it. Unsaved session state does
not continue across calls. Only unchanged native saves from this same batch may
be reopened; arbitrary external `.pcraft`/PSD imports and general resume are not
exposed. Caller cancellation does not promise rollback. Publication errors report
already-published outputs so they can be inspected rather than overwritten.

Input is bounded RGB/RGBA 8-bit non-interlaced PNG. The initial workflow permits
one open document at a time, up to 48 steps, 16 layers, 4096 pixels per side and
4,194,304 pixels per image, with a 16,777,216 aggregate layer-pixel budget. Input,
output, response and native-session time limits are enforced separately. Secure
file publication requires descriptor-relative no-follow support; unsupported
platforms fail before native editing rather than weakening these guards.

Core 0.20.44 is the tested minimum. The default adapter remains Python 3.9+;
optional live showcase tooling uses Python 3.10+ dependencies. The CLI is
headless native, not the desktop GUI. Actual native acceptance is qualified
against official Linux x86_64 PhotoCraft v0.8.2; other versions are discovered at
runtime but are not claimed tested merely because their schemas are compatible.
See [native workflow and qualification](docs/native-image-workflow.md).

### Web mode

The released [v0.8.2 web shell](https://github.com/storytold/photocraft/blob/d43db3e153c76aefce9b32cef9099e458eaf17a8/apps/photocraft-web/src/web.rs)
provides UI and file flows, but no reviewed exported live-document editing
command dispatcher was found. No Web adapter or maintained source overlay is
introduced here. Native MCP success does not qualify Web/WASM or desktop GUI.
