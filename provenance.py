import copy
import hashlib
import json
import os
from pathlib import Path

import folder_paths
from comfy_execution.graph_utils import is_link

from .png_bundle import ASSET_EXTENSIONS, FORMAT, VERSION


def upstream_graph(root, get_node):
    graph = {}
    pending = [root]
    while pending:
        node_id = pending.pop()
        if node_id in graph:
            continue
        node = get_node(node_id)
        graph[node_id] = copy.deepcopy(node)
        pending.extend(value[0] for value in node["inputs"].values() if is_link(value))
    return {node_id: graph[node_id] for node_id in sorted(graph)}


def collect_provenance(prompt, unique_id, workflow, loader_names, dynprompt=None):
    if not prompt:
        raise ValueError("Save Image with Provenance requires the execution prompt")
    root = unique_id
    if root not in prompt:
        if dynprompt is None:
            raise ValueError(f"Provenance output node is missing from the prompt: {root}")
        root = dynprompt.get_real_node_id(root)
    graph = upstream_graph(root, prompt.__getitem__)
    runtime_graph = upstream_graph(unique_id, dynprompt.get_node) if dynprompt is not None else graph
    assets = {}
    files = {}
    source_hashes = {}
    for node_id, node in runtime_graph.items():
        if node["class_type"] not in loader_names:
            continue
        filename = node["inputs"]["image"]
        if not isinstance(filename, str):
            raise ValueError(f"Cannot collect {node_id}.image: provenance v1 requires a literal image filename")
        path = folder_paths.get_annotated_filepath(filename)
        extension = Path(path).suffix.lower()
        if extension not in ASSET_EXTENSIONS:
            raise ValueError(f"Cannot restore image extension {extension!r} from node {node_id}")
        source = os.path.normcase(os.path.realpath(path))
        if source not in source_hashes:
            with open(path, "rb") as stream:
                data = stream.read()
            digest = hashlib.sha256(data).hexdigest()
            source_hashes[source] = digest
            entry = f"assets/{digest}"
            if digest not in assets:
                assets[digest] = {
                    "entry": entry, "sha256": digest, "size": len(data),
                    "extension": extension, "references": [],
                }
                files[entry] = data
        digest = source_hashes[source]
        prompt_node_id = node_id if node_id in graph and graph[node_id]["class_type"] == node["class_type"] else None
        assets[digest]["references"].append({
            "node_id": node_id, "prompt_node_id": prompt_node_id,
            "class_type": node["class_type"], "input": "image", "filename": filename,
        })
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "graph_scope": "connected-upstream-including-lazy-branches",
        "graph": graph,
        "workflow_scope": "original-unfiltered" if workflow is not None else "not-provided",
        "workflow": copy.deepcopy(workflow),
        "output": {"node_id": root, "runtime_node_id": unique_id},
        "assets": list(assets.values()),
    }
    if runtime_graph != graph:
        manifest["runtime_graph"] = runtime_graph
    return manifest, files


def describe_provenance(manifest):
    output = manifest["output"]
    lines = [
        f"ComfyUI provenance v{VERSION}",
        f"Output: {output['width']} x {output['height']}, batch {output['batch_index'] + 1}/{output['batch_size']}",
        "Generation graph: connected upstream nodes (unused lazy branches may remain).",
        "Editor workflow: original, unfiltered; use the generation graph for analysis.",
        "Input files must remain unchanged during generation. Model weights are not embedded.",
        "Automatic source collection covers standard LoadImage, LoadImageMask and LoadImageOutput only.",
        "",
        "Generation settings:",
    ]
    for node_id, node in manifest["graph"].items():
        title = node.get("_meta", {}).get("title", node["class_type"])
        lines.append(f"[{node_id}] {title} ({node['class_type']})")
        for name, value in node["inputs"].items():
            if not is_link(value):
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                lines.append(f"  {name}: {text}")
            else:
                lines.append(f"  {name}: node {value[0]}, output {value[1]}")
    lines.extend(["", "Embedded input files:"])
    for asset in manifest["assets"]:
        lines.append(f"  SHA-256 {asset['sha256']} ({asset['size']} bytes)")
        for ref in asset["references"]:
            lines.append(f"    node {ref['node_id']}: {ref['filename']}")
    if not manifest["assets"]:
        lines.append("  None from supported source loaders.")
    if manifest["workflow"] is None:
        lines.append("No editor workflow was supplied; the API prompt is available.")
    if "runtime_graph" in manifest:
        lines.append("Runtime node expansion/overrides are recorded separately; editor references may need manual restoration.")
    return "\n".join(lines)
