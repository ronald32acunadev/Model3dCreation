"""Converts the test cube with the real engine Python and bpy. Needs the engine environment."""

import os
import subprocess
from pathlib import Path

import pytest

from model3d_client.services.converter import SCRIPT

REPO = Path(__file__).resolve().parents[2]
ENGINE_PYTHON = REPO / "engine/.venv/bin/python"
CUBE = Path(__file__).resolve().parent / "assets" / "cube.glb"

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not ENGINE_PYTHON.exists(), reason="the engine environment is not installed"),
]

ENV = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
REIMPORT = (
    "import bpy, sys\n"
    "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
    "{import_op}(filepath=sys.argv[1])\n"
    "print('MESH_COUNT', sum(1 for o in bpy.data.objects if o.type == 'MESH'))\n"
)


def convert(target: Path) -> None:
    subprocess.run([str(ENGINE_PYTHON), str(SCRIPT), str(CUBE), str(target)], env=ENV, check=True, timeout=120)


def mesh_count(code: str, target: Path) -> int:
    result = subprocess.run([str(ENGINE_PYTHON), "-c", code, str(target)], env=ENV, check=True,
                            capture_output=True, text=True, timeout=120)
    marker = next(line for line in result.stdout.splitlines() if line.startswith("MESH_COUNT "))
    return int(marker.split()[1])


@pytest.mark.parametrize("suffix", [".fbx", ".obj", ".stl", ".usdz"])
def test_converts_cube_to_format(tmp_path, suffix):
    target = tmp_path / f"cube{suffix}"
    convert(target)
    assert target.stat().st_size > 0


def test_fbx_reimports_with_a_mesh(tmp_path):
    target = tmp_path / "cube.fbx"
    convert(target)
    assert mesh_count(REIMPORT.format(import_op="bpy.ops.import_scene.fbx"), target) > 0


def test_usdz_reimports_with_a_mesh(tmp_path):
    target = tmp_path / "cube.usdz"
    convert(target)
    assert mesh_count(REIMPORT.format(import_op="bpy.ops.wm.usd_import"), target) > 0
