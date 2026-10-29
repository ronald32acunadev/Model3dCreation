import json
import shutil
import struct
from pathlib import Path

import pytest

from model3d_client.paths import REPO_ROOT, app_paths
from model3d_client.views.viewer_widget import ViewerWidget

CUBE = Path(__file__).parent / "assets" / "cube.glb"


def _spinning_glb(path: Path) -> None:
    """Write a minimal GLB: one triangle on one node, animated by a 'Spin' rotation channel."""
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    times = struct.pack("<2f", 0.0, 1.0)
    rotations = struct.pack("<8f", 0, 0, 0, 1, 0, 0.70710678, 0, 0.70710678)
    binary = positions + times + rotations
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "animations": [{
            "name": "Spin",
            "samplers": [{"input": 1, "output": 2, "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}],
        }],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 2, "type": "SCALAR",
             "min": [0.0], "max": [1.0]},
            {"bufferView": 2, "componentType": 5126, "count": 2, "type": "VEC4"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 8},
            {"buffer": 0, "byteOffset": 44, "byteLength": 32},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode()
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
        + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    )


def _evaluate(qtbot, viewer, expression):
    result = []
    viewer.page().runJavaScript(expression, 0, result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=5000)
    return result[0]


def _js_poll(viewer, expression):
    """Return a callable that asks the page for a boolean expression and reports whether it is True."""
    values = []

    def poll():
        viewer.page().runJavaScript(expression, 0, values.append)
        return bool(values and values[-1] is True)

    return poll


@pytest.mark.webengine
def test_loads_glb_without_error(qtbot, tmp_path):
    library = tmp_path / "library"
    (library / "E1").mkdir(parents=True)
    shutil.copyfile(CUBE, library / "E1" / "model.glb")
    viewer = ViewerWidget(app_paths({}, repo=REPO_ROOT).viewer_dir, library)
    qtbot.addWidget(viewer)
    viewer.resize(400, 300)
    viewer.show()
    errors = []
    viewer.load_failed.connect(errors.append)
    viewer.show_model(library / "E1" / "model.glb")
    viewer.set_textured(False)
    viewer.reset_camera()

    loaded = []

    def poll():
        viewer.is_loaded(loaded.append)
        return bool(loaded and loaded[-1])

    qtbot.waitUntil(lambda: poll() or bool(errors), timeout=20000)
    assert errors == []
    assert loaded[-1] is True


@pytest.mark.webengine
def test_plays_animation_and_hides_control_for_plain_model(qtbot, tmp_path):
    library = tmp_path / "library"
    (library / "E2").mkdir(parents=True)
    (library / "E3").mkdir(parents=True)
    _spinning_glb(library / "E2" / "model.glb")
    shutil.copyfile(CUBE, library / "E3" / "model.glb")
    viewer = ViewerWidget(app_paths({}, repo=REPO_ROOT).viewer_dir, library)
    qtbot.addWidget(viewer)
    viewer.resize(400, 300)
    viewer.show()
    errors = []
    viewer.load_failed.connect(errors.append)

    loaded = []

    def poll():
        viewer.is_loaded(loaded.append)
        return bool(loaded and loaded[-1])

    viewer.show_model(library / "E2" / "model.glb")
    qtbot.waitUntil(lambda: poll() or bool(errors), timeout=20000)
    assert errors == []
    assert _evaluate(qtbot, viewer, "JSON.stringify(window.viewer.animations)") == '["Spin"]'
    assert _evaluate(qtbot, viewer, "window.viewer.playing") is True
    assert _evaluate(qtbot, viewer, """(() => {
        const el = document.getElementById('animation-toggle');
        return !el.hidden && getComputedStyle(el).display !== 'none';
    })()""") is True

    start = _evaluate(qtbot, viewer, "window.viewer.animationTime()")
    qtbot.wait(500)
    later = _evaluate(qtbot, viewer, "window.viewer.animationTime()")
    assert later > start

    _evaluate(qtbot, viewer, "window.viewer.setPlaying(false)")
    assert _evaluate(qtbot, viewer, "window.viewer.playing") is False
    paused_at = _evaluate(qtbot, viewer, "window.viewer.animationTime()")
    qtbot.wait(300)
    assert _evaluate(qtbot, viewer, "window.viewer.animationTime()") == paused_at

    # Loading a model without animations clears the clips and hides the control.
    viewer.show_model(library / "E3" / "model.glb")
    plain_loaded = _js_poll(
        viewer, "window.viewer.loaded === true && window.viewer.animations.length === 0"
    )
    qtbot.waitUntil(lambda: plain_loaded() or bool(errors), timeout=20000)
    assert errors == []
    assert _evaluate(qtbot, viewer, "JSON.stringify(window.viewer.animations)") == "[]"
    assert _evaluate(qtbot, viewer, "document.getElementById('animation-toggle').hidden") is True
