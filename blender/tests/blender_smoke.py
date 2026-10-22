"""Runs inside Blender: blender -b --factory-startup --python blender/tests/blender_smoke.py

Exits with status 1 on the first failed check (Blender itself exits 0 after a script error).
"""

import sys
import tempfile
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rigged_glb(folder: Path) -> Path:
    """A cube bound to a two-bone armature, exported as GLB; the scene is emptied afterwards."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    bpy.ops.object.armature_add()
    armature = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    tip = armature.data.edit_bones.new("tip")
    tip.head, tip.tail = (0, 0, 1), (0, 0, 2)
    tip.parent = armature.data.edit_bones["Bone"]
    bpy.ops.object.mode_set(mode="OBJECT")
    group = cube.vertex_groups.new(name="Bone")
    group.add(list(range(len(cube.data.vertices))), 1.0, "REPLACE")
    cube.parent = armature
    cube.modifiers.new("Armature", "ARMATURE").object = armature
    path = folder / "rigged.glb"
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return path


def main() -> None:
    import model3d
    from model3d import ui

    model3d.register()
    assert hasattr(bpy.types, "MODEL3D_OT_generate")
    assert hasattr(bpy.types, "MODEL3D_OT_cancel")
    assert hasattr(bpy.types, "MODEL3D_OT_refresh_library")
    assert hasattr(bpy.types, "MODEL3D_OT_import_entry")
    assert hasattr(bpy.types, "MODEL3D_OT_add_skeleton")
    assert hasattr(bpy.types, "MODEL3D_PT_main")
    assert hasattr(bpy.types, "MODEL3D_PT_library")
    assert hasattr(bpy.types.Scene, "model3d")
    with tempfile.TemporaryDirectory() as folder:
        path = rigged_glb(Path(folder))
        imported = ui.import_glb(path)
    armatures = [obj for obj in imported if obj.type == "ARMATURE"]
    assert len(armatures) == 1, [obj.type for obj in imported]
    assert bpy.context.view_layer.objects.active == armatures[0]
    assert all(obj.select_get() for obj in imported), [(obj.name, obj.type, obj.select_get(), obj.hide_get(), obj.hide_select) for obj in imported]
    model3d.unregister()
    assert not hasattr(bpy.types, "MODEL3D_OT_generate")
    assert not hasattr(bpy.types.Scene, "model3d")
    print("MODEL3D_SMOKE_OK")


try:
    main()
except Exception:
    traceback.print_exc()
    sys.exit(1)
