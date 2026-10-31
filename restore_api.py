import asyncio
import logging
import tempfile
import zipfile
from pathlib import Path

from aiohttp import web

import folder_paths

from .png_bundle import read_png_bundle_stream
from .restore import prepare_restore, write_restored_files


RESTORE_ROUTE = "/provenance/restore"


def restore_to_input(stream):
    manifest, files = read_png_bundle_stream(stream)
    input_dir = Path(folder_paths.get_input_directory())
    parent = input_dir / "provenance"
    if not folder_paths.is_within_directory(str(input_dir), str(parent)):
        raise ValueError("Provenance directory is outside the input directory")
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="restore_", dir=parent))
    try:
        if not folder_paths.is_within_directory(str(input_dir), str(destination)):
            raise ValueError("Restoration directory is outside the input directory")
        prefix = f"provenance/{destination.name}/assets"
        # The UI imports the filtered API graph, not the unfiltered editor workflow.
        prompt, _, references, warnings, contents = prepare_restore(
            {**manifest, "workflow": None}, files, image_prefix=prefix,
        )
        mapped = {ref.get("prompt_node_id") for ref in references if ref.get("prompt_node_id") is not None}
        for node_id, node in prompt.items():
            if node["class_type"] == "LoadImageOutput" and node_id in mapped:
                node["class_type"] = "LoadImage"
            if node["class_type"] in ("LoadImage", "LoadImageMask", "LoadImageOutput") and node_id not in mapped:
                warnings.append(f"Source node {node_id} has no mapped embedded image; select its image manually.")
        write_restored_files(destination, contents)
    except (OSError, ValueError):
        try:
            destination.rmdir()
        except OSError:
            logging.exception("Could not remove incomplete restoration directory: %s", destination)
        raise
    return {
        "prompt": prompt, "warnings": warnings,
        "references": references, "directory": f"provenance/{destination.name}",
    }


async def restore_image(request):
    if request.content_type != "multipart/form-data":
        return web.json_response({"error": "Upload a PNG as multipart field 'image'."}, status=400)
    try:
        post = await request.post()
    except web.HTTPRequestEntityTooLarge as error:
        return web.json_response({"error": error.text}, status=413)
    except ValueError as error:
        return web.json_response({"error": f"Invalid multipart upload: {error}"}, status=400)
    images = post.getall("image", [])
    if len(images) != 1 or not isinstance(images[0], web.FileField) or set(post) != {"image"}:
        return web.json_response({"error": "Exactly one PNG file in field 'image' is required."}, status=400)
    image = images[0]
    try:
        result = await asyncio.to_thread(restore_to_input, image.file)
    except (ValueError, UnicodeError, zipfile.BadZipFile) as error:
        return web.json_response({"error": str(error)}, status=400)
    except OSError:
        logging.exception("Could not restore provenance input images")
        return web.json_response({"error": "Could not write restored images. Check the ComfyUI log."}, status=500)
    finally:
        image.file.close()
    return web.json_response(result)


def register_restore_routes(routes):
    routes.post(RESTORE_ROUTE)(restore_image)
