"""Review renders and numbers for one run: input, current model, and kit model side by side.

Run with the engine's interpreter, which ships bpy: `python compare.py RUN_DIR`. Writes
compare_front.png, compare_head.png, exploded.png and compare.json (texture density per
material, in texture pixels per body height, measured as in docs/userstories/staged-factory/).
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

SIZE = 900
EXPLODE = 0.08  # glTF units each piece moves along its joint normal
COLORS = [(0.75, 0.75, 0.75), (0.9, 0.35, 0.3), (0.3, 0.6, 0.9), (0.3, 0.8, 0.45), (0.95, 0.75, 0.25), (0.7, 0.4, 0.85)]


def blender_point(point) -> tuple[float, float, float]:
    """glTF (x, y, z) to Blender world (x, -z, y)."""
    return point[0], -point[2], point[1]


def setup(bpy):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.8, 0.8, 0.8, 1.0)
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (0.6, 0.0, 0.3)
    scene.collection.objects.link(sun)
    camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    camera.data.type = "ORTHO"
    scene.collection.objects.link(camera)
    scene.camera = camera
    return scene, camera


def shoot(bpy, scene, camera, center, scale: float, path: Path) -> None:
    """Front orthographic render (the subject faces +Z in glTF, -Y in Blender)."""
    x, y, z = blender_point(center)
    camera.data.ortho_scale = scale
    camera.location = (x, y - 10.0, z)
    camera.rotation_euler = (np.pi / 2, 0.0, 0.0)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def render_model(bpy, glb: Path, views: dict[str, tuple], out: Path) -> None:
    scene, camera = setup(bpy)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    for name, (center, scale) in views.items():
        shoot(bpy, scene, camera, center, scale, out / f"{name}.png")


def render_exploded(bpy, run: Path, rig: dict, view: tuple, path: Path) -> None:
    scene, camera = setup(bpy)
    for index, name in enumerate(rig["parts"]):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(run / "pieces" / f"{name}.glb"))
        material = bpy.data.materials.new(name)
        material.diffuse_color = (*COLORS[index % len(COLORS)], 1.0)
        material.use_nodes = True
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*COLORS[index % len(COLORS)], 1.0)
        offset = np.zeros(3) if name == "torso" else EXPLODE * np.asarray(rig["joints"][name]["normal"])
        for obj in set(bpy.data.objects) - before:
            if obj.type == "MESH":
                obj.data.materials.clear()
                obj.data.materials.append(material)
                obj.location = tuple(np.asarray(obj.location) + np.asarray(blender_point(offset)))
    shoot(bpy, scene, camera, *view, path)


def density(glb: Path, height: float) -> dict[str, float]:
    """Texture pixels per body height for each material: size * sqrt(UV area / surface area) * height."""
    scene = trimesh.load(glb)
    meshes = scene.geometry.values() if isinstance(scene, trimesh.Scene) else [scene]
    result = {}
    for index, mesh in enumerate(meshes):
        material = getattr(mesh.visual, "material", None)
        image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
        uv = getattr(mesh.visual, "uv", None)
        if image is None or uv is None:
            continue
        corners = uv[mesh.faces]
        uv_area = 0.5 * np.abs(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])).sum()
        name = getattr(material, "name", None) or f"material_{index}"
        result[name] = float(max(image.size) * np.sqrt(uv_area / mesh.area) * height)
    return result


def sheet(paths: list[Path], titles: list[str], out: Path) -> None:
    tiles = [Image.open(p).convert("RGB") for p in paths]
    tiles = [t.resize((round(t.width * SIZE / t.height), SIZE)) for t in tiles]
    image = Image.new("RGB", (sum(t.width for t in tiles) + 10 * (len(tiles) - 1), SIZE + 40), (40, 40, 40))
    x = 0
    for tile, title in zip(tiles, titles):
        image.paste(tile, (x, 40))
        ImageDraw.Draw(image).text((x + 8, 12), title, fill=(255, 255, 255))
        x += tile.width + 10
    image.save(out)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: compare.py RUN_DIR", file=sys.stderr)
        return 2
    import bpy

    run = Path(argv[1])
    rig = json.loads((run / "rig.json").read_text())
    crops = json.loads((run / "crops.json").read_text())
    mold = trimesh.load(run / "mold.glb", force="mesh")
    labels = np.load(run / "mold_labels.npy")
    lo, hi = mold.bounds
    height = float(hi[1] - lo[1])
    head = mold.vertices[labels == rig["parts"].index("head")] if "head" in rig["parts"] else mold.vertices
    head_lo, head_hi = head.min(axis=0), head.max(axis=0)
    views = {
        "front": ((lo + hi) / 2, float(max(hi[0] - lo[0], height) * 1.05)),
        "head": ((head_lo + head_hi) / 2, float(max(head_hi[:2] - head_lo[:2]) * 1.15)),
    }
    renders = run / "renders"
    for name, glb in (("current", run / "mold.glb"), ("kit", run / "model.glb")):
        (renders / name).mkdir(parents=True, exist_ok=True)
        render_model(bpy, glb, views, renders / name)
    render_exploded(bpy, run, rig, (views["front"][0], views["front"][1] * 1.2), run / "exploded.png")

    image = Image.open(run / "input.png").convert("RGBA")
    flat = Image.new("RGBA", image.size, (204, 204, 204, 255))
    flat.alpha_composite(image)
    flat.convert("RGB").save(renders / "input_front.png")
    if "head" in crops["parts"]:
        x0, y0, x1, y1 = crops["parts"]["head"]["mask_box"]
        side = max(x1 - x0, y1 - y0) * 1.15
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        flat.crop((round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2))).convert("RGB").save(renders / "input_head.png")
    sheet([renders / "input_front.png", renders / "current/front.png", renders / "kit/front.png"],
          ["input", "current (TRELLIS.2 high)", "kit"], run / "compare_front.png")
    if (renders / "input_head.png").exists():
        sheet([renders / "input_head.png", renders / "current/head.png", renders / "kit/head.png"],
              ["input", "current (TRELLIS.2 high)", "kit"], run / "compare_head.png")
    report = {"height": height, "density_current": density(run / "mold.glb", height), "density_kit": density(run / "model.glb", height)}
    (run / "compare.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    # bpy can hang in interpreter teardown after the work is written; exit without it.
    os._exit(code)
