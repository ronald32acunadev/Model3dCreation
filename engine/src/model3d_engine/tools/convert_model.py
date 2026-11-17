"""Converts a GLB model to FBX, OBJ, STL, or USDZ.

Runs with the engine's Python, which ships bpy: `python convert_model.py SOURCE.glb TARGET.ext`.
This file must not import model3d_client; the client starts it as a separate process.
"""

import sys
from pathlib import Path

# suffix -> (bpy operator as "module.name", keyword options)
EXPORTERS = {
    ".fbx": ("export_scene.fbx", {"path_mode": "COPY", "embed_textures": True, "add_leaf_bones": False}),
    ".obj": ("wm.obj_export", {"export_materials": True, "path_mode": "COPY"}),
    ".stl": ("wm.stl_export", {}),
    ".usdz": ("wm.usd_export", {"export_textures": True}),
}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: convert_model.py SOURCE.glb TARGET.ext", file=sys.stderr)
        return 2
    source, target = Path(argv[1]), Path(argv[2])
    suffix = target.suffix.lower()
    if suffix not in EXPORTERS:
        print(f"Unsupported format: {suffix}", file=sys.stderr)
        return 2

    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "FINISHED" not in bpy.ops.import_scene.gltf(filepath=str(source)):
        print(f"Could not import {source}", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    operator, options = EXPORTERS[suffix]
    group, name = operator.split(".")
    export = getattr(getattr(bpy.ops, group), name)
    if "FINISHED" not in export(filepath=str(target), **options):
        print(f"Could not write {target}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
