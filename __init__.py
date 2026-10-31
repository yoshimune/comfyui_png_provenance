import copy
import json
import os

from PIL.PngImagePlugin import PngInfo

import folder_paths
import nodes
from comfy.cli_args import args
from comfy_api.latest import ComfyExtension, io, ui
from server import PromptServer

from .png_bundle import CHUNK_TYPE, FORMAT, VERSION, encode_bundle
from .provenance import collect_provenance, describe_provenance
from .restore_api import register_restore_routes


WEB_DIRECTORY = "./web"


class SaveImageWithProvenance(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SaveImageWithProvenance",
            display_name="Save Image with Provenance",
            category="image",
            description="Save a PNG with generation settings and original input image files embedded.",
            inputs=[
                io.Image.Input("images"),
                io.String.Input("filename_prefix", default="ComfyUI"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id, io.Hidden.dynprompt],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, images, filename_prefix="ComfyUI"):
        if args.disable_metadata:
            raise ValueError("Save Image with Provenance requires metadata. Remove --disable-metadata.")
        loader_names = {
            name for name, node in nodes.NODE_CLASS_MAPPINGS.items()
            if node in (nodes.LoadImage, nodes.LoadImageMask, nodes.LoadImageOutput)
        }
        extra = cls.hidden.extra_pnginfo
        workflow = extra.get("workflow") if extra is not None else None
        manifest, files = collect_provenance(
            cls.hidden.prompt, cls.hidden.unique_id, workflow, loader_names, cls.hidden.dynprompt,
        )
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory(), images[0].shape[1], images[0].shape[0],
        )
        results = []
        for batch_index, image in enumerate(images):
            item = copy.copy(manifest)
            item["output"] = {
                **manifest["output"], "batch_index": batch_index, "batch_size": len(images),
                "width": image.shape[1], "height": image.shape[0],
            }
            metadata = PngInfo()
            metadata.add_text("ComfyProvenanceFormat", f"{FORMAT}/{VERSION}")
            metadata.add_itxt("Description", describe_provenance(item))
            metadata.add_text("prompt", json.dumps(item["graph"]))
            if workflow is not None:
                metadata.add_text("workflow", json.dumps(workflow))
            metadata.add(CHUNK_TYPE, encode_bundle(item, files), after_idat=True)
            file = f"{filename.replace('%batch_num%', str(batch_index))}_{counter:05}_.png"
            img = ui.ImageSaveHelper._convert_tensor_to_pil(image)
            img.save(os.path.join(folder, file), pnginfo=metadata, compress_level=4)
            results.append(ui.SavedResult(file, subfolder, io.FolderType.output))
            counter += 1
        return io.NodeOutput(ui=ui.SavedImages(results))


class ProvenanceExtension(ComfyExtension):
    async def on_load(self):
        register_restore_routes(PromptServer.instance.routes, PromptServer.instance.sockets)

    async def get_node_list(self):
        return [SaveImageWithProvenance]


async def comfy_entrypoint():
    return ProvenanceExtension()
