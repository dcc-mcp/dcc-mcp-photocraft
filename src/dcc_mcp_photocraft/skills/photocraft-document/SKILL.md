---
name: photocraft-document
description: Edit layered PhotoCraft documents and save native projects or image exports in an isolated headless session.
license: MIT
metadata:
  dcc-mcp:
    dcc: photocraft
    layer: domain
    version: "0.1.0a1"
    tools: tools.yaml
    search-hint: "PhotoCraft document layers masks text adjustment resize preview export native pcraft"
    links:
      repo: https://github.com/dcc-mcp/dcc-mcp-photocraft
      issues: https://github.com/dcc-mcp/dcc-mcp-photocraft/issues
---

Use this experimental skill for PhotoCraft 0.2.0 headless document editing.
Start with connection_status and document_session. Paths are relative to the
operator-provided input/output roots. Inputs remain unchanged; outputs require
new filenames. Native .pcraft is the editable deliverable. Preserve export
warnings and verify the native document after close and reopen. External input
is PNG/JPEG only; native reopen accepts this session's tracked output files.
PSD fidelity is outside this adapter's supported surface.

Start an async operation once and query its existing Core job until terminal.
A transport failure can leave an edit or file write incomplete: never replay
it automatically. The managed session becomes unavailable on transport failure.
Save the manifest returned by connection_status before shutting down. The
service has no cross-session recovery store; smoke scripts save their manifest.
PhotoCraft native operations are monolithic and cannot be interrupted safely;
Core cancellation before admission is supported, mid-call cancellation is not
claimed. No GUI, desktop bridge, upstream jobs, cloud generation or raw command
execution is enabled. The official desktop bridge can replay edits after a
transport error, so it is not interchangeable with this headless route.
