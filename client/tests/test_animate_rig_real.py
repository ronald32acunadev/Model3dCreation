"""Detects and animates rigged models with the real engine Python and bpy. Needs the engine environment."""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENGINE_PYTHON = REPO / "engine/.venv/bin/python"
SCRIPT = Path(__file__).resolve().parents[1] / "src/model3d_client/services/animate_rig.py"
SPACE_DOG = REPO / "data/jobs/01M64AEQF3CZX1VAY4SBX5SVZQ/model.glb"
TAG = "MODEL3D_RESULT "

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not ENGINE_PYTHON.exists(), reason="the engine environment is not installed"),
]

ENV = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}

# Builds a tall box (z 0..1.4) and parents it to the armature with automatic weights.
MESH_BLOCK = """
body = bpy.data.objects.new("body", bpy.data.meshes.new("body"))
bpy.context.collection.objects.link(body)
verts = [(x, y, z) for x in (-0.4, 0.4) for y in (-0.6, 0.6) for z in (0.0, 1.4)]
body.data.from_pydata(verts, [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
body.data.update()
"""

RIG_HEADER = """
import sys
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
arm_data = bpy.data.armatures.new("Rig")
arm = bpy.data.objects.new("Rig", arm_data)
bpy.context.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones
"""

RIG_FOOTER = """
bpy.ops.object.mode_set(mode="OBJECT")
"""

QUADRUPED = RIG_HEADER + """
root = eb.new("root")
root.head = (0.0, 0.0, 1.0)
root.tail = (0.0, 0.0, 1.2)
for name, x, y in [("fl", 0.3, 0.5), ("fr", 0.3, -0.5), ("bl", -0.3, 0.5), ("br", -0.3, -0.5)]:
    upper = eb.new(name + "_upper")
    upper.head = (x, y, 1.0)
    upper.tail = (x, y, 0.5)
    upper.parent = root
    lower = eb.new(name + "_lower")
    lower.head = (x, y, 0.5)
    lower.tail = (x, y, 0.0)
    lower.parent = upper
""" + RIG_FOOTER + MESH_BLOCK + """
body.parent = arm
mod = body.modifiers.new("Armature", "ARMATURE")
mod.object = arm
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
for stray in [o for o in bpy.data.objects if o.name not in ("body", "Rig")]:
    bpy.data.objects.remove(stray, do_unlink=True)
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format="GLB")
"""

NO_LEGS = RIG_HEADER + """
root = eb.new("root")
root.head = (0.0, 0.0, 0.7)
root.tail = (0.0, 0.0, 0.9)
previous = root
for index in range(3):
    bone = eb.new(f"row_{index}")
    bone.head = (-0.6 + index * 0.4, 0.0, 0.7)
    bone.tail = (-0.2 + index * 0.4, 0.0, 0.7)
    bone.parent = previous
    previous = bone
""" + RIG_FOOTER + MESH_BLOCK + """
body.parent = arm
body.modifiers.new("Armature", "ARMATURE").object = arm
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
for stray in [o for o in bpy.data.objects if o.name not in ("body", "Rig")]:
    bpy.data.objects.remove(stray, do_unlink=True)
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format="GLB")
"""

CUBE = """
import sys
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format="GLB")
"""

REIMPORT_WALK = """
import json
import sys
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=sys.argv[-1])
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
action = bpy.data.actions.get("Walk")
paths = [fc.data_path for fc in action.fcurves] if action else []
bone_23 = arm.pose.bones["bone_23"]
scene = bpy.context.scene
positions = {}
for frame in (0, 6):
    scene.frame_set(frame)
    positions[frame] = list(arm.matrix_world @ bone_23.tail)
print("WALK_CHECK " + json.dumps({
    "action": action is not None,
    "bone_20": any('"bone_20"' in p for p in paths),
    "bone_24": any('"bone_24"' in p for p in paths),
    "frame0": positions[0],
    "frame6": positions[6],
}))
"""


def run_script(code: str, tmp_path: Path, output: Path) -> None:
    script = tmp_path / "build.py"
    script.write_text(code, encoding="utf-8")
    subprocess.run([str(ENGINE_PYTHON), str(script), str(output)], env=ENV, check=True,
                   capture_output=True, timeout=180)


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ENGINE_PYTHON), str(SCRIPT), *args], env=ENV,
                          capture_output=True, text=True, timeout=180)


def result_of(proc: subprocess.CompletedProcess) -> dict:
    line = next(line for line in proc.stdout.splitlines() if line.startswith(TAG))
    return json.loads(line[len(TAG):])


@pytest.mark.skipif(not SPACE_DOG.exists(), reason="the space dog fixture is not in data/")
def test_space_dog_is_a_biped_with_two_arms():
    result = result_of(run_tool("detect", str(SPACE_DOG)))
    assert result["body"] == "biped"
    assert sorted(result["legs"]) == sorted([
        ["bone_20", "bone_21", "bone_22", "bone_23"],
        ["bone_24", "bone_25", "bone_26", "bone_27"],
    ])
    assert len(result["arms"]) == 2


def test_synthetic_quadruped_is_detected(tmp_path):
    model = tmp_path / "quadruped.glb"
    run_script(QUADRUPED, tmp_path, model)
    result = result_of(run_tool("detect", str(model)))
    assert result["body"] == "quadruped"
    assert len(result["legs"]) == 4


def test_chain_without_legs_is_unknown(tmp_path):
    model = tmp_path / "rowless.glb"
    run_script(NO_LEGS, tmp_path, model)
    result = result_of(run_tool("detect", str(model)))
    assert result["body"] == "unknown"
    assert result["legs"] == []


def test_model_without_armature_has_no_skeleton(tmp_path):
    model = tmp_path / "cube.glb"
    run_script(CUBE, tmp_path, model)
    proc = run_tool("detect", str(model))
    assert proc.returncode == 1
    assert "The model has no skeleton" in proc.stderr


@pytest.mark.skipif(not SPACE_DOG.exists(), reason="the space dog fixture is not in data/")
def test_walk_adds_a_looping_action_that_moves_the_legs(tmp_path):
    target = tmp_path / "walk.glb"
    proc = run_tool("walk", str(SPACE_DOG), str(target), "biped")
    assert proc.returncode == 0, proc.stderr
    assert result_of(proc)["frames"] == 24

    check = subprocess.run([str(ENGINE_PYTHON), "-c", REIMPORT_WALK, str(target)], env=ENV,
                           capture_output=True, text=True, timeout=180, check=True)
    line = next(line for line in check.stdout.splitlines() if line.startswith("WALK_CHECK "))
    data = json.loads(line[len("WALK_CHECK "):])
    assert data["action"] is True
    assert data["bone_20"] is True
    assert data["bone_24"] is True
    assert data["frame0"] != pytest.approx(data["frame6"], abs=1e-4)


def test_walk_rejects_unknown_body(tmp_path):
    proc = run_tool("walk", str(SPACE_DOG), str(tmp_path / "walk.glb"), "dragon")
    assert proc.returncode == 2
