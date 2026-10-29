import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.blender
SMOKE = Path(__file__).with_name("blender_smoke.py")


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender is not on PATH")
def test_addon_registers_imports_a_rigged_glb_and_unregisters():
    done = subprocess.run(
        ["blender", "-b", "--factory-startup", "--python", str(SMOKE)],
        capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "MODEL3D_SMOKE_OK" in done.stdout


def test_manifest_validates():
    if shutil.which("blender") is None:
        pytest.skip("blender is not on PATH")
    done = subprocess.run(
        ["blender", "--command", "extension", "validate", str(Path(__file__).parents[1] / "model3d")],
        capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
