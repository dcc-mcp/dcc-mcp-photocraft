---
name: photocraft-core
description: >-
  Domain skill — inspect PhotoCraft documents, discover the command and method
  tables the installed PhotoCraft publishes, and drive the running PhotoCraft
  desktop app.
license: MIT
compatibility: "PhotoCraft 0.2+; dcc-mcp-core 0.20.36+"
allowed-tools: "python"
metadata:
  dcc-mcp:
    dcc: photocraft
    layer: domain
    version: "0.1.0"
    search-hint: "photocraft document_info list_photocraft_commands list_photocraft_methods ui_screenshot ui_inspect"
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
`list_photocraft_commands` before assuming a command exists.** The ids accepted
by this skill are resolved against the table the installed host reports, not
against a list frozen at adapter release time, so a command that appears upstream
becomes reachable without an adapter release.

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
