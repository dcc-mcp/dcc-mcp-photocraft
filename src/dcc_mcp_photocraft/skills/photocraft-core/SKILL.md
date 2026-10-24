---
name: photocraft-core
description: >-
  Domain skill — run bounded native image workflows, inspect documents, discover the command and method
  tables the installed PhotoCraft publishes, and drive the running PhotoCraft
  desktop app.
license: MIT
compatibility: "PhotoCraft CLI with runtime-discovered capabilities; dcc-mcp-core 0.20.44+"
allowed-tools: "python"
metadata:
  dcc-mcp:
    dcc: photocraft
    layer: domain
    version: "0.1.0"
    search-hint: "photocraft document_info list_photocraft_commands list_photocraft_methods ui_screenshot ui_inspect execute_photocraft_image_batch"
    tags: "photocraft,image,editing,raster"
    tools: tools.yaml
---

# PhotoCraft Core

Inspect and drive [PhotoCraft](https://github.com/storytold/photocraft), the
open-source image editor.

## Headless first

`document_info`, `list_photocraft_commands` and `list_photocraft_methods` run
through `photocraft-cli` and need no window, display server, or running app. This
is the path to prefer: it is the only one that works in CI and on a headless
server.

## Discover, do not assume

PhotoCraft is pre-1.0 and its command table grows continuously. **Call
`list_photocraft_commands` before assuming a command exists.** Discovery reads the table the installed host reports, not a catalog frozen at
adapter release time. New upstream commands become visible to discovery; the
image workflow still exposes only its reviewed typed translations.

## Driving the live app

`ui_screenshot` and `ui_inspect` need the desktop app started with
`--control <port>`, plus the token it was launched with. Point
`PHOTOCRAFT_CONTROL_PORT` at the port and supply the credential through
`PHOTOCRAFT_CONTROL_TOKEN` or `PHOTOCRAFT_CONTROL_TOKEN_FILE`. When no instance is
attached these actions fail with guidance rather than returning an empty result.

Unlike the sibling PdfCraft adapter, PhotoCraft does not write its port to a
file: the port is the one you pass to `--control`, so it is configured rather
than discovered. Because every Craft app listens on loopback, choose a port that
does not collide with the others.

Upstream requires an `auth` frame as the first request on every connection, and
refuses to dispatch anything before it succeeds. The port alone is therefore not
enough — a token is always required, and if you did not pass one at launch
upstream generated one and wrote it to standard error.


## Bounded native editing

Use `execute_photocraft_image_batch` for one complete original-PNG workflow.
It accepts only typed `steps`, at most 48. Supported op values: `open_png`,
`place_png`, `set_layer`, `mask_rectangle`, `set_mask`, `curves`, `inspect`,
`save_project`, `close`, `reopen_project`, `export_png`.

1. Open a PNG under the configured read root, assigning document and base-layer
   aliases. Place additional original PNGs as embedded Smart Objects with new
   layer aliases. Only one document may be open at once.
2. Apply bounded layer properties, editable masks and Curves adjustment layers.
   Inspect after changes; verify that hiding an adjustment restores the expected
   baseline and that masks remain editable.
3. Save to a new `.pcraft` output, close and reopen only that unchanged owned save.
   Revisions and indices can reset; never supply raw numeric identity guesses.
4. Inspect and continue editing through the verified aliases, then export a new
   PNG. Independently compare pixels and hashes, and verify inputs are unchanged.
5. Failure is not rollback. Do not replay automatically. Inspect completed steps
   and any published outputs first; in-memory work does not persist across calls.

Inputs and outputs are relative to separate launcher-configured read/write roots.
No overwrite, arbitrary command/JSON facade, external native project import,
control_call, GUI or Web editing is provided by this workflow. Actual native
qualification is Linux x86_64 v0.8.2; discovery of another version is not testing.
The native task uses current MCP tool schemas; engine params use reviewed local
bounds because the host's engine catalog only has prose parameter descriptions.

Before discovery or a document operation, the native image session requires the
negotiated MCP protocol, the official `photocraft` server name and a stable
version >=0.2.0. This is a compatibility check of self-reported identity, not
binary provenance or qualification of every future release. Preserve the
`native_mcp` receipt. Windows fails the image batch before native launch because
the required descriptor-relative no-follow publication primitives are absent;
use its separately tested catalog/document readback rather than replaying it.

The adapter can discover native installs recorded by ArtCraft Launcher without
starting either application. `PHOTOCRAFT_LAUNCHER_DATA` may identify the actual
data folder containing `installed.json` for a portable launcher. Keep official
installation/update authority with the launcher. A path is a hint, not a launch
grant, artifact verification or a live desktop binding.

For a cross-software showcase, preserve artifact checksums, observed PNG color
metadata and decoded pixel readback. Untagged synthetic sRGB-intended samples
require explicit sRGB interpretation in the consumer; they are not scene-linear
EXR. FilmCraft, CAD/DCC and Nuke imports remain separate acceptance steps. A
PhotoCraft `.pcraft` project is not Photoshop binary compatibility.
