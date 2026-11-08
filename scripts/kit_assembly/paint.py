"""Give each piece of the fused surface its own UVs, material, and 4096 px texture.

Run with the engine's interpreter, which ships bpy: `python paint.py RUN_DIR [--size 4096]`.
Reads fused.glb, fused_labels.npy, kit.json, rig.json, mold.glb and aligned/<part>.glb; writes
model.glb and paint.json. Each part's texture is baked from its own generated model; texels its
surface does not reach are baked from the mold, and their share is recorded.
"""

import argparse
import json
import os
import math
import sys
import time
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

CAGE_EXTRUSION = 0.004  # glTF units; the body is about 1.0 tall and the fused grid is about 0.002
MAX_RAY_DISTANCE = 0.012
MARGIN_PX = 8


def gltf_point(vector) -> tuple[float, float, float]:
    return vector[0], vector[2], -vector[1]


def import_glb(bpy, path: Path):
    """Import a GLB and return its mesh objects joined into one."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def blend_weight(bpy, target, source, plane: dict, blend: float, size: int) -> np.ndarray:
    """Per-texel weight of the part's color: 0 at the cut plane, 1 at `blend` above it (smoothstep).

    It matches kit.py's geometry blend, so the colors meet where the shapes do (the bust's brighter
    shirt otherwise showed as a band above the dog's cut). Baked as emission from the part's surface.
    """
    point, normal = blender_vector(plane["point"]), blender_vector(plane["normal"])
    material = bpy.data.materials.new("blend_weight")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    geometry = tree.nodes.new("ShaderNodeNewGeometry")
    dot = tree.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = normal
    offset = tree.nodes.new("ShaderNodeMath")
    offset.operation = "SUBTRACT"
    offset.inputs[1].default_value = float(np.dot(point, normal))
    ramp = tree.nodes.new("ShaderNodeMapRange")
    ramp.interpolation_type = "SMOOTHSTEP"
    ramp.inputs["From Min"].default_value = 0.0
    ramp.inputs["From Max"].default_value = blend
    emission = tree.nodes.new("ShaderNodeEmission")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(geometry.outputs["Position"], dot.inputs[0])
    tree.links.new(dot.outputs["Value"], offset.inputs[0])
    tree.links.new(offset.outputs["Value"], ramp.inputs["Value"])
    tree.links.new(ramp.outputs["Result"], emission.inputs["Color"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])

    originals = list(source.data.materials)
    for index in range(len(originals)):
        source.data.materials[index] = material
    image = bpy.data.images.new("weight", size, size, alpha=True)
    image.pixels.foreach_set(np.zeros(size * size * 4, np.float32))
    bake(bpy, target, source, image, clear=False, kind="EMIT")
    for index, original in enumerate(originals):
        source.data.materials[index] = original
    weight = pixels(image)[:, :, 0].copy()
    bpy.data.images.remove(image)
    bpy.data.materials.remove(material)
    return np.clip(weight, 0.0, 1.0)


def blender_vector(vector) -> tuple[float, float, float]:
    """glTF (x, y, z) to Blender world (x, -z, y)."""
    return float(vector[0]), -float(vector[2]), float(vector[1])


def bake(bpy, target, source, image, clear: bool, kind: str = "DIFFUSE") -> None:
    """Bake the source's base color (or emission) onto the target's active image node."""
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    node = target.active_material.node_tree.nodes["Bake"]
    node.image = image
    target.active_material.node_tree.nodes.active = node
    bpy.ops.object.bake(
        type=kind, pass_filter={"COLOR"}, use_selected_to_active=True,
        cage_extrusion=CAGE_EXTRUSION, max_ray_distance=MAX_RAY_DISTANCE,
        margin=MARGIN_PX, use_clear=clear,
    )


def pixels(image) -> np.ndarray:
    buffer = np.empty(image.size[0] * image.size[1] * 4, np.float32)
    image.pixels.foreach_get(buffer)
    return buffer.reshape(image.size[1], image.size[0], 4)


def island_mask(obj, size: int) -> np.ndarray:
    """Texels covered by the object's UV triangles (image rows bottom-up, as Blender stores them)."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    uv = obj.data.uv_layers.active.data
    for polygon in obj.data.polygons:
        draw.polygon([(uv[i].uv[0] * size, uv[i].uv[1] * size) for i in polygon.loop_indices], fill=1)
    return np.asarray(mask, bool)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--size", type=int, default=4096)
    args = parser.parse_args(argv)
    run, size = Path(args.run), args.size
    started = time.monotonic()
    kit = json.loads((run / "kit.json").read_text())
    if "skipped" in kit:
        (run / "paint.json").write_text(json.dumps({"skipped": kit["skipped"]}, indent=2))
        print(json.dumps({"skipped": kit["skipped"]}))
        return 0
    rig = json.loads((run / "rig.json").read_text())
    names = rig["parts"]

    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1

    fused = import_glb(bpy, run / "fused.glb")
    fused.name = "model"
    reference = trimesh.load(run / "fused.glb", force="mesh")
    labels = np.load(run / "fused_labels.npy")
    centers = np.array([gltf_point(fused.matrix_world @ p.center) for p in fused.data.polygons])
    _, nearest = cKDTree(np.asarray(reference.triangles_center)).query(centers)
    face_labels = labels[nearest]

    for name in names:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        tree = material.node_tree
        principled = tree.nodes["Principled BSDF"]
        principled.inputs["Roughness"].default_value = 0.7
        principled.inputs["Metallic"].default_value = 0.0
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = "Bake"
        tree.links.new(node.outputs["Color"], principled.inputs["Base Color"])
        fused.data.materials.append(material)
    fused.data.polygons.foreach_set("material_index", face_labels.astype(np.int32))
    fused.data.update()

    bpy.ops.object.select_all(action="DESELECT")
    fused.select_set(True)
    bpy.context.view_layer.objects.active = fused
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="MATERIAL")
    bpy.ops.object.mode_set(mode="OBJECT")
    targets = [o for o in bpy.data.objects if o.type == "MESH" and (o is fused or o.name.startswith("model"))]

    mold = import_glb(bpy, run / "mold.glb")
    sources = {}
    for name in names:
        chosen = kit["volume_check"].get(name, {}).get("file")
        sources[name] = import_glb(bpy, run / "aligned" / chosen) if chosen else mold

    report = {}
    for target in targets:
        bpy.ops.object.select_all(action="DESELECT")
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.material_slot_remove_unused()
        name = target.active_material.name
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.002)
        bpy.ops.object.mode_set(mode="OBJECT")

        base = bpy.data.images.new(f"{name}_mold", size, size, alpha=True)
        bake(bpy, target, mold, base, clear=True)
        color = pixels(base)
        part_share = None
        if sources[name] is not mold:
            own = bpy.data.images.new(f"{name}_part", size, size, alpha=True)
            own.pixels.foreach_set(np.zeros(size * size * 4, np.float32))
            bake(bpy, target, sources[name], own, clear=False)
            own_pixels = pixels(own)
            hit = own_pixels[:, :, 3] > 0.5
            weight = np.ones(color.shape[:2], np.float32)
            plane = kit.get("planes", {}).get(name)
            if plane is not None:
                weight = blend_weight(bpy, target, sources[name], plane, kit["blend"], size)
            mixed = weight[hit][:, None]
            color[hit] = mixed * own_pixels[hit] + (1 - mixed) * color[hit]
            islands = island_mask(target, size)
            part_share = float((hit & islands).sum() / max(islands.sum(), 1))
            bpy.data.images.remove(own)
        color[:, :, 3] = 1.0
        final = bpy.data.images.new(f"{name}_color", size, size, alpha=False)
        final.pixels.foreach_set(color.ravel())
        final.pack()
        target.active_material.node_tree.nodes["Bake"].image = final
        bpy.data.images.remove(base)
        report[name] = {"faces": len(target.data.polygons), "texture_px": size,
                        "part_texel_share": part_share,
                        "mold_texel_share": None if part_share is None else 1.0 - part_share}
        print(json.dumps({name: report[name]}), flush=True)

    for source in {id(s): s for s in sources.values()}.values():
        bpy.data.objects.remove(source, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for target in targets:
        target.select_set(True)
    bpy.context.view_layer.objects.active = targets[0]
    bpy.ops.object.join()
    # Marching-cubes faces read as facets with flat normals; export smooth vertex normals.
    bpy.ops.object.shade_smooth()
    bpy.ops.export_scene.gltf(filepath=str(run / "model.glb"), export_format="GLB", use_selection=True,
                              export_image_format="JPEG", export_jpeg_quality=92)
    report["seconds"] = round(time.monotonic() - started, 1)
    (run / "paint.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # bpy can hang in interpreter teardown after the work is written; exit without it.
    os._exit(code)
