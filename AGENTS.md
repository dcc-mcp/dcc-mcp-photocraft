# AGENTS.md — dcc-mcp-photocraft

PhotoCraft adapter for the DCC Model Context Protocol. Navigation map for AI
agents, not a reference manual. Follow the links; do not read everything up
front.

## What this adapter is

PhotoCraft has **no embedded Python interpreter**. Unlike the Maya, Blender or
Houdini adapters, this package never runs inside the host — it drives PhotoCraft
as an external process over two surfaces:

* **`photocraft-cli`** — a headless subprocess. Primary path; no window, no
  display server, and the only path that works in CI.
* **The desktop app's control channel** — `photocraft --control <port>` binds a
  loopback port and requires an `auth` frame before any other request. Used only
  for UI actions.

Both are optional. A machine with only the CLI installed is usable, not broken:
every headless capability works.

## The rule that matters most

**Do not hardcode the PhotoCraft command set.** Upstream ships new minor versions
continuously and its command table grows with them. Command ids are resolved at
runtime against the table the installed host reports, via
`list_photocraft_commands` / `photocraft-cli commands --json`. A hardcoded list is
stale on the next upstream release.

The same applies to version assertions in tests: never assert an exact upstream
version.

## How this differs from the sibling PdfCraft adapter

The two adapters are protocol siblings, and the places they differ are the places
a copy goes wrong:

* **Port source.** PdfCraft writes its port and token to a control file on each
  start, so the adapter re-reads that file per call. PhotoCraft binds the port
  given to `--control <port>` (or `PHOTOCRAFT_CONTROL_PORT`), so the port comes
  from configuration instead. There is no file to re-read.
* **Authentication.** Both require an `auth` frame as the first request on every
  connection; upstream refuses to dispatch anything before it succeeds. The
  difference is only in how the credential reaches the adapter: PdfCraft reads it
  from its control file, while PhotoCraft takes it from
  `PHOTOCRAFT_CONTROL_TOKEN` or `PHOTOCRAFT_CONTROL_TOKEN_FILE`. A port alone is
  never sufficient.
* **Reply envelope.** PhotoCraft answers `{"ok": false, "error": "..."}` rather
  than a JSON-RPC 2.0 error object, so failures are read from the `ok` flag.
* **File access.** PhotoCraft takes its read and write authorities as two
  independent flags (`--automation-read-root` / `--automation-write-root`) and
  fails closed when the applicable one was not granted at launch, rather than
  taking a single sandbox root.

## Build and test

```bash
python -m pip install -e ".[dev]"   # editable install with dev extras
python -m pytest                    # run the test suite
ruff check src tests                # lint
ruff format --check src tests       # format check (line-length 120, py39 target)
python -m build --no-isolation      # build wheel + sdist
```

Tests never require PhotoCraft to be installed and never reach the network. The
control channel is exercised against an in-process fake server
(`tests/conftest.py`), so the auth handshake and line framing are proven against a
real socket rather than a mock.

## Layout

| Path | Purpose |
| --- | --- |
| `src/dcc_mcp_photocraft/env.py` | Environment variable names. A leaf module: the transports read config from here so they never import `server` back. |
| `src/dcc_mcp_photocraft/control_channel.py` | Loopback JSON-lines RPC client with the `auth` handshake. Dependency-free by design. |
| `src/dcc_mcp_photocraft/cli_transport.py` | `photocraft-cli` subprocess driver. Drives `commands`, `info` and `run` as one-shot subcommands, and `serve` as a JSON-lines stdio session. |
| `src/dcc_mcp_photocraft/host.py` | Locating the binaries on PATH and in per-platform install directories. |
| `src/dcc_mcp_photocraft/capability_dispatch.py` | Routes skill actions to the right host surface. |
| `src/dcc_mcp_photocraft/actions.py` | Adapter-owned actions (`host_status`, `list_photocraft_commands`, …). |
| `src/dcc_mcp_photocraft/server.py` | `DccServerBase` composition and lifecycle. |
| `src/dcc_mcp_photocraft/doctor.py` | Read-only self-check. |
| `src/dcc_mcp_photocraft/skills/photocraft-core/` | Shipped skill: `SKILL.md` + `tools.yaml` + `scripts/dispatch.py`. |

## Conventions

* `requires-python` is `>=3.9`. The control-channel client is deliberately
  dependency-free so it does not inherit a third-party SDK's Python floor; the
  optional `mcp` extra is gated on `python_version >= "3.10"` for the same
  reason.
* The control port is chosen by whoever launches the app, not by the app. The
  six sibling Craft apps all listen on loopback, so pick a port that does not
  collide with the others.
* The control token is a secret. Never log or echo it; the doctor reports only
  that one was configured.
* Host detection is a filesystem lookup, never a subprocess spawn.

## Two CLI shapes

`photocraft-cli` is not uniform, and mixing the two shapes up is the easiest way
to write a method that silently never works:

* **One-shot subcommands** take their input in argv and print JSON to stdout:
  `commands --json`, `info <file>`, `run --new <json> --cmd <id>`. These go
  through `CliTransport.run`.
* **`serve` is a session.** It keeps one headless engine session open and answers
  JSON-lines requests on stdin/stdout, so the request is written to the
  process's stdin rather than passed as arguments. It goes through
  `CliTransport._serve_lines`. Its replies use the same `{"ok": false, "error":
  ...}` envelope as the control channel, not a JSON-RPC error object.

Both the engine command table (`commands --json`) and the headless method list
(`serve` -> `methods`) are read from the installed host at call time. Neither is
mirrored in this repository.
