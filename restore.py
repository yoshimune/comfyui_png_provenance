import argparse
import copy
import json
import logging
import os
from pathlib import Path

if __package__:
    from .png_bundle import read_png_bundle
else:
    from png_bundle import read_png_bundle


def restore_png(png_path, output_dir):
    manifest, files = read_png_bundle(png_path)
    prompt = copy.deepcopy(manifest["graph"])
    workflow = copy.deepcopy(manifest["workflow"])
    warnings = []
    references = []
    contents = {}
    workflow_nodes = {}
    if workflow is not None:
        if isinstance(workflow.get("nodes"), list):
            for node in workflow["nodes"]:
                if not isinstance(node, dict) or not isinstance(node.get("id"), (str, int)):
                    raise ValueError("Invalid editor workflow node in provenance")
                node_id = str(node["id"])
                if node_id in workflow_nodes:
                    raise ValueError(f"Duplicate editor workflow node in provenance: {node_id}")
                workflow_nodes[node_id] = node
        else:
            warnings.append("Editor workflow has no top-level node list; restore its image references manually.")
    for asset in manifest["assets"]:
        filename = f"assets/{asset['sha256']}{asset['extension']}"
        contents[filename] = files[asset["entry"]]
        for ref in asset["references"]:
            references.append({**ref, "restored_filename": filename})
            node_id = ref["prompt_node_id"]
            if node_id is None:
                warnings.append(f"Runtime source {ref['node_id']} has no direct editor/API node; restore {filename} manually.")
                continue
            node = prompt.get(node_id)
            if node is None or node["class_type"] != ref["class_type"]:
                raise ValueError(f"Invalid API source reference in provenance: {node_id}")
            node["inputs"][ref["input"]] = filename
            if workflow is not None:
                editor_node = workflow_nodes.get(node_id)
                if (
                    editor_node is not None and editor_node.get("type") == ref["class_type"]
                    and isinstance(editor_node.get("widgets_values"), list) and editor_node["widgets_values"]
                ):
                    editor_node["widgets_values"][0] = filename
                else:
                    warnings.append(f"Editor source {node_id} could not be mapped; select {filename} manually.")
    contents["prompt.json"] = json.dumps(prompt, ensure_ascii=False, indent=2).encode("utf-8")
    if workflow is not None:
        contents["workflow.original.json"] = json.dumps(manifest["workflow"], ensure_ascii=False, indent=2).encode("utf-8")
        contents["workflow.json"] = json.dumps(workflow, ensure_ascii=False, indent=2).encode("utf-8")
    contents["references.json"] = json.dumps({
        "references": references, "warnings": warnings, "output": manifest["output"],
        "image_placement": "Copy the restored assets directory into ComfyUI's input directory.",
    }, ensure_ascii=False, indent=2).encode("utf-8")
    root = Path(output_dir).resolve()
    targets = []
    for name, data in contents.items():
        target = root / name
        if not target.resolve().is_relative_to(root):
            raise ValueError(f"Restore path is outside the destination: {target}")
        if os.path.lexists(target):
            raise FileExistsError(f"Refusing to overwrite restored file: {target}")
        targets.append((target, data))
    root.mkdir(parents=True, exist_ok=True)
    for target, data in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.resolve().is_relative_to(root):
            raise ValueError(f"Restore path is outside the destination: {target}")
        with open(target, "xb") as stream:
            stream.write(data)
    return warnings


def main():
    parser = argparse.ArgumentParser(description="Restore generation data and original inputs embedded in a ComfyUI PNG.")
    parser.add_argument("png", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    warnings = restore_png(args.png, args.destination)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.info("Restored to %s", args.destination)
    logging.info("Copy the assets directory into ComfyUI's input directory before using prompt.json or workflow.json.")
    for warning in warnings:
        logging.warning("Manual restoration required: %s", warning)


if __name__ == "__main__":
    main()
