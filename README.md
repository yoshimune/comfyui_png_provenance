# Save Image with Provenance

## Purpose

**Save Image with Provenance** is an output node that saves generated images as
PNG files, like Save Image, and embeds provenance in each PNG. It records the
upstream generation graph, the editor workflow when available, a readable
description of generation settings, and the original files used by supported
image loaders. This keeps the image and its provenance together in one file.

## Add the node to ComfyUI

1. Copy the `comfyui_png_provenance` folder, including its `__init__.py`, into
   your ComfyUI installation's `custom_nodes` folder. The resulting path should
   be `ComfyUI\custom_nodes\comfyui_png_provenance\__init__.py`.
2. Restart ComfyUI. Refreshing the browser alone does not load a new custom node.
3. In your workflow, replace the final Save Image node with **Save Image with
   Provenance**. Connect the final `IMAGE` output to `images`, set
   `filename_prefix`, and queue the workflow.

If the node does not appear, check the ComfyUI startup log for custom-node
loading errors. `--disable-metadata` must be removed to use this node.

Each output is a normal PNG, including each image of a batch. No sidecar files,
network requests, model downloads, or changes to standard saving are involved.

## What is recorded

- `prompt`: the saving node and its connected upstream generation graph.
- `workflow`: the original editor workflow, when supplied, for normal ComfyUI
  PNG drag-and-drop loading. This is **unfiltered restoration data**, not the
  graph to use for analysis.
- `Description`: readable generation settings and an input-file inventory.
  Unicode, including Japanese prompts, is preserved. Viewer support for this
  field varies; Eagle integration is not included.
- Original file bytes used by standard **LoadImage**, **LoadImageMask** and
  **LoadImageOutput**, deduplicated by SHA-256 within the save call.

Disconnected nodes and other output branches are excluded from `prompt`.
Connected but unused lazy branches can remain. Node expansion/overrides are
recorded separately as `runtime_graph` when different from the submitted graph.
Arbitrary custom loaders, non-image assets, model weights, and custom-node code
are not collected. Connected filename inputs cannot be resolved by this node;
supported source loaders must use literal filenames.

**Do not change or delete input files during generation.** The saving node
re-reads the source files; its hashes verify the embedded bytes, not their
identity with an earlier loading operation. Original input metadata is retained,
so review the embedded information before sharing a PNG.

## Format v1

The text keyword `ComfyProvenanceFormat` is `comfyui-png-provenance/1`.
`Description` is UTF-8 iTXt. `prompt` and `workflow` are standard JSON text fields.

A private ancillary PNG chunk, **cpRV**, after IDAT contains:

| Offset | Content |
|---|---|
| 0 | ASCII `COMFY-PROVENANCE` followed by NUL (17 bytes) |
| 17 | Unsigned 16-bit big-endian version, currently 1 |
| 19 | ZIP archive using ZIP_STORED |

The ZIP contains UTF-8 `manifest.json` and original files at `assets/<sha256>`.
The manifest has `format`, `version`, `graph_scope`, `graph`, `workflow_scope`,
`workflow`, `output`, and `assets`; `runtime_graph` is optional. Each asset has
`entry`, `sha256`, `size`, `extension`, and `references`. Each reference identifies
the runtime `node_id`, corresponding `prompt_node_id` when available,
`class_type`, `input`, and original `filename`.

Analyze **graph**, not the unfiltered **workflow**. `output` identifies the
original/runtime saving node and zero-based batch position. Models, seeds,
sampling parameters, and text are available as inputs of their respective nodes;
there is no assumption that a workflow contains only one sampler.

Image optimizers, editors, and viewers may discard custom metadata when
re-saving. Keep the original generated PNG for restoration.

## Restore locally

The CLI uses only Python's standard library; it does not require torch or a
running ComfyUI instance:

```powershell
python custom_nodes\comfyui_png_provenance\restore.py output\example.png restored
```

It validates the PNG chunk CRCs, format version, ZIP structure, asset sizes, and
SHA-256 hashes. It never overwrites an existing file or writes to the source paths
from metadata.

The destination contains `assets`, `prompt.json`, `references.json`, and, when an
editor workflow was supplied, `workflow.original.json` and `workflow.json`.
Copy the restored **assets directory** into `ComfyUI\input`, then load
`workflow.json` in ComfyUI or submit `prompt.json` using the normal API.
Standard loader filenames are rewritten to `assets/<sha256>.<extension>`,
including sources originally loaded from output/temp folders.

Expanded/subgraph sources without a direct editor-node mapping require manual
selection of the restored file. The CLI prints these warnings and also records
them in `references.json`; it does not guess how to edit an unfamiliar workflow.
Input bundles are not recursively extracted.

The editor workflow still contains unrelated nodes and branches. Use
`prompt.json` to replay only the saved generation branch, or adjust those other
branches before queuing the restored editor workflow.

Supply the same model files and custom nodes separately. Randomness, runtime
versions, devices, and attention backends can prevent exact reproduction.
File size includes the entire original source files and their existing metadata.
