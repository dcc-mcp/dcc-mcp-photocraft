---
name: photocraft-document
description: Import images as editable layers, crop and arrange pixels, create local masks, reduce noise, and save native PhotoCraft projects in an isolated headless session.
license: MIT
metadata:
  dcc-mcp:
    dcc: photocraft
    layer: domain
    version: "0.1.0a1"
    tools: tools.yaml
    search-hint: "PhotoCraft document image import layers crop canvas transform local mask selection denoise reduce noise despeckle text adjustment preview export native pcraft"
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

Complete crop, canvas expansion, image resizing and layer geometry while the
document contains eligible pixel layers, before adding masks, tonal layers or
type. Geometry rejects unknown mask bounds, selections, linked layers and
unsupported layer types. These are adapter limits, not claims that the native
application lacks those features. Inspect layer IDs before selecting, arranging
or duplicating them. Canvas expansion is transparent; affine scale/rotation use
the document origin. The capability query describes the full pinned release
inventory and separates exposed operations from blocked or unexposed commands.

Image import uses the native scoped sequence: open the staged PNG/JPEG in a
temporary document, copy its pixels, select the original target, paste at the
original pixel coordinates, close only the temporary source, and purge the
internal clipboard. It creates a real editable Pixel layer. This sequence is
not atomic and is not a single undo step; a partial failure must not be replayed.

For local masking, create a selection and reveal/hide it through mask_set, then
clear the selection and continue editing the mask with mask_paint. Select the
target as the single active layer before mask_enabled, mask_paint, selection_fill
or noise operations. Noise reduction and despeckle edit Pixel content and respect
the current selection. A layer mask does not automatically restrict a filter:
use selection_from_mask to load its coverage, apply noise_reduce/noise_despeckle,
and then selection_clear. Preserve the mask and verify native reopen, including
mask disable/enable pixel comparisons when proving editable local effects.

Start an async operation once and query its existing Core job until terminal.
A transport failure can leave an edit or file write incomplete: never replay
it automatically. The managed session becomes unavailable on transport failure.
Any uncertain admitted operation also latches a persistent recovery requirement;
later edits and artifact writes are blocked. Only connection_status,
document_inspect, capabilities_query and document_session(action="list") remain
available for diagnosis. Successful inspection does not clear this requirement.
Save the manifest returned by connection_status before shutting down. The
service has no cross-session recovery store; smoke scripts save their manifest.
PhotoCraft native operations are monolithic and cannot be interrupted safely;
Core cancellation before admission is supported, mid-call cancellation is not
claimed. No GUI, desktop bridge, upstream jobs, cloud generation or raw command
execution is enabled. The official desktop bridge can replay edits after a
transport error, so it is not interchangeable with this headless route.
