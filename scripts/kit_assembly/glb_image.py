"""Read and replace the images inside a GLB without touching its meshes, skins or animations.

A GLB is a 12-byte header, a JSON chunk and a binary chunk. Images live in buffer views of the
binary chunk; replacing one rewrites the binary chunk with the new bytes and moves every later
buffer view, so meshes, skeletons and accessors stay exactly as they were.
"""

import json
import struct
from pathlib import Path

MAGIC = 0x46546C67  # "glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def _align(size: int) -> int:
    return (size + 3) & ~3


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = Path(path).read_bytes()
    magic, version, _ = struct.unpack_from("<III", data, 0)
    if magic != MAGIC or version != 2:
        raise ValueError(f"{path} is not a glTF 2.0 binary")
    offset, document, binary = 12, None, b""
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        if kind == JSON_CHUNK:
            document = json.loads(chunk)
        elif kind == BIN_CHUNK:
            binary = chunk
        offset += 8 + length
    if document is None:
        raise ValueError(f"{path} has no JSON chunk")
    return document, binary


def write_glb(path: Path, document: dict, binary: bytes) -> None:
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (_align(len(text)) - len(text))
    binary += b"\0" * (_align(len(binary)) - len(binary))
    total = 12 + 8 + len(text) + (8 + len(binary) if binary else 0)
    parts = [struct.pack("<III", MAGIC, 2, total), struct.pack("<II", len(text), JSON_CHUNK), text]
    if binary:
        parts += [struct.pack("<II", len(binary), BIN_CHUNK), binary]
    Path(path).write_bytes(b"".join(parts))


def base_color_images(document: dict) -> dict[str, int]:
    """Image index of each material's base color texture, by material name."""
    found = {}
    for index, material in enumerate(document.get("materials", [])):
        texture = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if texture is None:
            continue
        source = document["textures"][texture["index"]].get("source")
        if source is not None:
            found[material.get("name", f"material_{index}")] = source
    return found


def image_bytes(document: dict, binary: bytes, image: int) -> bytes:
    view = document["bufferViews"][document["images"][image]["bufferView"]]
    start = view.get("byteOffset", 0)
    return binary[start:start + view["byteLength"]]


def replace_image(document: dict, binary: bytes, image: int, data: bytes, mime: str) -> tuple[dict, bytes]:
    """The document and binary chunk with one image's bytes replaced; nothing else changes."""
    document = json.loads(json.dumps(document))
    views = document["bufferViews"]
    target = document["images"][image]["bufferView"]
    pieces, offset = [], 0
    for index, view in sorted(enumerate(views), key=lambda item: item[1].get("byteOffset", 0)):
        if view.get("buffer", 0) != 0:
            continue
        start = view.get("byteOffset", 0)
        chunk = data if index == target else binary[start:start + view["byteLength"]]
        pad = _align(offset) - offset
        pieces.append(b"\0" * pad)
        offset += pad
        view["byteOffset"] = offset
        view["byteLength"] = len(chunk)
        pieces.append(chunk)
        offset += len(chunk)
    document["images"][image]["mimeType"] = mime
    document["buffers"][0]["byteLength"] = offset
    return document, b"".join(pieces)
