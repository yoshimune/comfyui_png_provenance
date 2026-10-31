import hashlib
import json
import struct
import zipfile
import zlib
from io import BytesIO


FORMAT = "comfyui-png-provenance"
VERSION = 1
CHUNK_TYPE = b"cpRV"
MAGIC = b"COMFY-PROVENANCE\0"
HEADER = MAGIC + struct.pack(">H", VERSION)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ASSET_EXTENSIONS = frozenset({
    ".png", ".apng", ".jpg", ".jpeg", ".jpe", ".jfif", ".mpo", ".webp",
    ".gif", ".bmp", ".dib", ".tif", ".tiff", ".avif", ".avifs", ".heic",
    ".heif", ".jxl", ".jp2", ".j2k", ".jpc", ".jpf", ".jpx", ".j2c",
    ".ico", ".cur", ".ppm", ".pgm", ".pbm", ".pnm", ".pfm", ".qoi",
    ".tga", ".dds", ".psd", ".exr", ".hdr", ".pic", ".rgb", ".rgba",
    ".sgi", ".bw", ".pcx", ".im", ".msp", ".ras", ".xbm", ".xpm",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg",
})


def validate_manifest(manifest, files):
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValueError("Unsupported provenance manifest format or version")
    for name in ("graph", "runtime_graph"):
        graph = manifest.get(name)
        if name == "runtime_graph" and graph is None:
            continue
        if not isinstance(graph, dict) or any(
            not isinstance(node_id, str) or not isinstance(node, dict)
            or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict)
            for node_id, node in graph.items()
        ):
            raise ValueError(f"Invalid provenance {name}")
    if "workflow" not in manifest or (manifest["workflow"] is not None and not isinstance(manifest["workflow"], dict)):
        raise ValueError("Invalid provenance workflow")
    output = manifest.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("node_id"), str) or output["node_id"] not in manifest["graph"]:
        raise ValueError("Missing provenance output information")
    if not isinstance(manifest.get("assets"), list):
        raise ValueError("Invalid provenance asset list")
    entries = set()
    for asset in manifest["assets"]:
        if not isinstance(asset, dict):
            raise ValueError("Invalid provenance asset")
        digest = asset.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid provenance asset SHA-256")
        entry = f"assets/{digest}"
        extension = asset.get("extension")
        if asset.get("entry") != entry or entry in entries or not isinstance(extension, str) or extension not in ASSET_EXTENSIONS:
            raise ValueError(f"Invalid or duplicate provenance asset: {entry}")
        entries.add(entry)
        data = files.get(entry)
        if data is None or asset.get("size") != len(data) or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Missing or corrupt provenance asset: {entry}")
        refs = asset.get("references")
        if not isinstance(refs, list) or not refs or any(
            not isinstance(ref, dict) or not isinstance(ref.get("node_id"), str)
            or not isinstance(ref.get("class_type"), str) or ref.get("input") != "image"
            or not isinstance(ref.get("filename"), str)
            or (ref.get("prompt_node_id") is not None and not isinstance(ref["prompt_node_id"], str))
            for ref in refs
        ):
            raise ValueError(f"Invalid provenance asset references: {entry}")
    if entries != set(files):
        raise ValueError("Unexpected files in provenance bundle")


def encode_bundle(manifest, files):
    validate_manifest(manifest, files)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    data = HEADER + buffer.getvalue()
    if len(data) > 0x7fffffff:
        raise ValueError("Provenance bundle exceeds the PNG chunk size limit")
    return data


def decode_bundle(data):
    if len(data) < len(HEADER) or data[:len(MAGIC)] != MAGIC:
        raise ValueError("Invalid provenance bundle header")
    version = struct.unpack(">H", data[len(MAGIC):len(HEADER)])[0]
    if version != VERSION:
        raise ValueError(f"Unsupported provenance bundle version: {version}")
    archive_data = data[len(HEADER):]
    with zipfile.ZipFile(BytesIO(archive_data)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Duplicate entries or missing manifest in provenance bundle")
        if any(info.compress_type != zipfile.ZIP_STORED or info.compress_size != info.file_size for info in infos):
            raise ValueError("Provenance v1 requires uncompressed ZIP entries")
        if sum(info.file_size for info in infos) > len(archive_data):
            raise ValueError("Invalid provenance ZIP entry sizes")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        files = {name: archive.read(name) for name in names if name != "manifest.json"}
    validate_manifest(manifest, files)
    return manifest, files


def read_png_bundle(path):
    with open(path, "rb") as stream:
        return read_png_bundle_stream(stream)


def read_png_bundle_stream(stream):
    bundle = None
    if stream.read(8) != PNG_SIGNATURE:
        raise ValueError("Not a PNG file")
    while True:
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError("Truncated PNG chunk header")
        length, chunk_type = struct.unpack(">I4s", header)
        if length > 0x7fffffff:
            raise ValueError("Invalid PNG chunk length")
        if chunk_type == CHUNK_TYPE:
            if bundle is not None:
                raise ValueError("Duplicate PNG provenance chunks")
            bundle = bytearray()
        crc = zlib.crc32(chunk_type)
        remaining = length
        while remaining:
            part = stream.read(min(remaining, 1024 * 1024))
            if not part:
                raise ValueError("Truncated PNG chunk data")
            crc = zlib.crc32(part, crc)
            if chunk_type == CHUNK_TYPE:
                bundle.extend(part)
            remaining -= len(part)
        stored_crc = stream.read(4)
        if len(stored_crc) != 4 or struct.unpack(">I", stored_crc)[0] != crc:
            raise ValueError(f"Invalid PNG chunk CRC: {chunk_type!r}")
        if chunk_type == b"IEND":
            if length or stream.read(1):
                raise ValueError("Invalid PNG ending")
            break
    if bundle is None:
        raise ValueError("PNG does not contain a ComfyUI provenance bundle")
    return decode_bundle(bundle)
