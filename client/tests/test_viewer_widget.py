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


def _cube_glb(path: Path, metallic: float) -> None:
    """Write a GLB unit cube (-0.5..0.5) with one PBR material of the given metallic factor."""
    faces = [
        ((1, 0, 0), (0, 1, 0), (0, 0, 1)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
        ((0, 1, 0), (0, 0, 1), (1, 0, 0)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
        ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    ]
    pos, nor, idx = [], [], []
    for n, u, v in faces:
        base = len(pos)
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            pos.append(tuple(0.5 * (n[i] + a * u[i] + b * v[i]) for i in range(3)))
            nor.append(n)
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    positions = struct.pack("<72f", *[c for p in pos for c in p])
    normals = struct.pack("<72f", *[c for n in nor for c in n])
    indices = struct.pack("<36H", *idx)
    binary = positions + normals + indices
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0,
        }]}],
        "materials": [{"pbrMetallicRoughness": {
            "baseColorFactor": [0.2, 0.2, 0.2, 1],
            "metallicFactor": metallic,
            "roughnessFactor": 0.4,
        }}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 24, "type": "VEC3",
             "min": [-0.5] * 3, "max": [0.5] * 3},
            {"bufferView": 1, "componentType": 5126, "count": 24, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 288},
            {"buffer": 0, "byteOffset": 288, "byteLength": 288},
            {"buffer": 0, "byteOffset": 576, "byteLength": 72},
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


def _model_luma(viewer) -> float:
    """Mean luma of the pixels that differ from the viewer background, sampled every 2nd pixel."""
    image = viewer.grab().toImage()
    background = (0x2B / 255, 0x2D / 255, 0x31 / 255)
    total = 0.0
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            r, g, b = color.redF(), color.greenF(), color.blueF()
            if abs(r - background[0]) + abs(g - background[1]) + abs(b - background[2]) > 0.03:
                total += 0.2126 * r + 0.7152 * g + 0.0722 * b
                count += 1
    return total / count if count else 0.0


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


@pytest.mark.webengine
def test_metallic_model_is_not_rendered_black(qtbot, tmp_path):
    library = tmp_path / "library"
    lumas = {}
    for metallic in (1.0, 0.0):
        folder = library / f"M{int(metallic)}"
        folder.mkdir(parents=True)
        _cube_glb(folder / "model.glb", metallic)
        viewer = ViewerWidget(app_paths({}, repo=REPO_ROOT).viewer_dir, library)
        qtbot.addWidget(viewer)
        viewer.resize(400, 300)
        viewer.show()
        errors = []
        viewer.load_failed.connect(errors.append)
        viewer.show_model(folder / "model.glb")
        viewer.reset_camera()

        loaded = []

        def poll():
            viewer.is_loaded(loaded.append)
            return bool(loaded and loaded[-1])

        qtbot.waitUntil(lambda: poll() or bool(errors), timeout=20000)
        assert errors == []
        qtbot.wait(1500)
        lumas[metallic] = _model_luma(viewer)
        viewer.close()

    metal_luma = lumas[1.0]
    plain_luma = lumas[0.0]
    assert metal_luma >= 0.4 * plain_luma, (
        f"metallic model luma {metal_luma:.3f} is below 40% of plain model luma {plain_luma:.3f}"
    )
