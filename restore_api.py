import asyncio
import logging
import tempfile
import weakref
import zipfile
from pathlib import Path

from aiohttp import ClientConnectionError, web

import folder_paths

from .png_bundle import read_png_bundle_stream
from .restore import prepare_restore, write_restored_files


RESTORE_ROUTE = "/provenance/restore"
CLIENTS_ROUTE = "/provenance/clients"
REGISTER_ROUTE = "/provenance/clients/register"
RESTORED_EVENT = "provenance.restored"


def registered_socket(client_id, sockets, ready_clients):
    socket = sockets.get(client_id)
    if socket is None or socket.closed or ready_clients.get(client_id) is not socket:
        ready_clients.pop(client_id, None)
        return None
    return socket


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


async def restore_image(request, sockets, ready_clients):
    if request.content_type != "multipart/form-data":
        return web.json_response({"error": "Upload a PNG as multipart field 'image'."}, status=400)
    try:
        post = await request.post()
    except web.HTTPRequestEntityTooLarge as error:
        return web.json_response({"error": error.text}, status=413)
    except ValueError as error:
        return web.json_response({"error": f"Invalid multipart upload: {error}"}, status=400)
    images = post.getall("image", [])
    if len(images) != 1 or not isinstance(images[0], web.FileField) or set(post) - {"image", "client_id"}:
        return web.json_response({"error": "Exactly one 'image' file and an optional 'client_id' are allowed."}, status=400)
    client_id = None
    socket = None
    if "client_id" in post:
        ids = post.getall("client_id")
        if len(ids) != 1 or not isinstance(ids[0], str) or not ids[0].strip():
            return web.json_response({"error": "client_id must be one non-empty string."}, status=400)
        client_id = ids[0]
        socket = registered_socket(client_id, sockets, ready_clients)
        if socket is None:
            return web.json_response({"error": "Target browser is not connected and registered."}, status=409)
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
    if client_id is not None:
        notification = {"client_id": client_id, "status": "failed"}
        error = None
        if registered_socket(client_id, sockets, ready_clients) is not socket:
            error = "Images were restored, but the target browser disconnected or changed connection."
        else:
            try:
                await socket.send_json({
                    "type": RESTORED_EVENT,
                    "data": {"prompt": result["prompt"], "name": "Restored workflow", "warnings": result["warnings"]},
                })
            except (ClientConnectionError, ConnectionError, RuntimeError):
                logging.exception("Could not notify provenance browser %s", client_id)
                error = "Images were restored, but notification to the target browser failed."
        if error is not None:
            return web.json_response({**result, "error": error, "notification": notification}, status=409)
        result["notification"] = {**notification, "status": "sent"}
    return web.json_response(result)


def register_restore_routes(routes, sockets):
    ready_clients = weakref.WeakValueDictionary()

    @routes.post(REGISTER_ROUTE)
    async def register_client(request):
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "A JSON object with client_id is required."}, status=400)
        if not isinstance(data, dict) or set(data) != {"client_id"} or not isinstance(data["client_id"], str) or not data["client_id"].strip():
            return web.json_response({"error": "A JSON object with one non-empty client_id is required."}, status=400)
        client_id = data["client_id"]
        socket = sockets.get(client_id)
        if socket is None or socket.closed:
            return web.json_response({"error": "Browser WebSocket is not connected."}, status=409)
        ready_clients[client_id] = socket
        return web.json_response({"client_id": client_id, "registered": True})

    @routes.get(CLIENTS_ROUTE)
    async def list_clients(request):
        return web.json_response({"clients": [
            {"client_id": client_id} for client_id in sorted(ready_clients)
            if registered_socket(client_id, sockets, ready_clients) is not None
        ]})

    @routes.post(RESTORE_ROUTE)
    async def restore(request):
        return await restore_image(request, sockets, ready_clients)
