"""End-to-end acceptance on the real engine: needs both GPUs free and the weights in data/hf."""

import json
import socket
import struct
from pathlib import Path

import pytest

from fakes import StubViewer
from model3d_client.app import build_app
from model3d_client.services.library import Library, Outcome
from model3d_client.services.supervisor import EngineState

pytestmark = pytest.mark.gpu

PENGUIN = Path(__file__).resolve().parents[2] / "engine/tests/assets/penguin.png"


def engine_answers() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=1):
            return True
    except OSError:
        return False


def start_app(qtbot):
    if engine_answers():
        pytest.skip("an engine already runs on 8765")
    _app, window = build_app([], viewer_factory=StubViewer)
    window.ask_close_anyway = lambda: True
    qtbot.addWidget(window)
    vm = window.vm
    vm.supervisor.start()
    qtbot.waitUntil(lambda: vm.supervisor.state == EngineState.ready, timeout=300_000)
    return vm


def stop_app(qtbot, vm) -> None:
    done = []
    vm.shutdown(lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=60_000)
    qtbot.waitUntil(lambda: not engine_answers(), timeout=30_000)


def wait_finished(qtbot, vm, action, timeout=600_000):
    with qtbot.waitSignal(vm.generation.finished, timeout=timeout) as blocker:
        action()
    return blocker.args[0]


def generate(qtbot, vm, *, texture: bool, rig: bool):
    vm.generation.set_image(PENGUIN)
    vm.generation.set_texture(texture)
    vm.generation.set_rig(rig)
    assert vm.generation.can_generate
    return wait_finished(qtbot, vm, vm.generation.generate)


def glb_json(data: bytes) -> dict:
    assert data[:4] == b"glTF"
    length, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A  # "JSON"
    return json.loads(data[20 : 20 + length])


def assert_rigged(path: Path) -> None:
    document = glb_json(path.read_bytes())
    assert len(document.get("skins", [])) == 1
    assert len(document["skins"][0]["joints"]) >= 10


def test_generates_penguin_end_to_end(qtbot):
    vm = start_app(qtbot)
    entry = generate(qtbot, vm, texture=True, rig=False)
    try:
        assert entry.outcome == Outcome.succeeded
        glb = vm.library.model_path(entry.entry_id)
        assert glb.read_bytes()[:4] == b"glTF"
        assert entry.entry_id in [e.entry_id for e in Library(vm.paths.library).entries()]
    finally:
        vm.delete(entry.entry_id)  # leave the user's library as it was
    stop_app(qtbot, vm)


def test_skeleton_on_new_job_and_on_library_model(qtbot):
    vm = start_app(qtbot)
    assert vm.generation.rig_available, vm.generation.rig_reason
    created = []
    try:
        plain = generate(qtbot, vm, texture=False, rig=False)
        created.append(plain.entry_id)
        assert plain.outcome == Outcome.succeeded, plain.error
        plain_bytes = vm.library.model_path(plain.entry_id).read_bytes()
        assert "skins" not in glb_json(plain_bytes)
        assert vm.generation.can_add_skeleton(plain)

        derived = wait_finished(qtbot, vm, lambda: vm.generation.add_skeleton(plain.entry_id))
        created.append(derived.entry_id)
        assert derived.outcome == Outcome.succeeded, derived.error
        assert derived.source_entry_id == plain.entry_id
        assert derived.name == plain.name + " (rigged)"
        assert derived.options.rig
        assert_rigged(vm.library.model_path(derived.entry_id))
        # The original entry and its model stay as they were.
        assert vm.library.model_path(plain.entry_id).read_bytes() == plain_bytes
        assert not vm.generation.can_add_skeleton(derived)

        rigged = generate(qtbot, vm, texture=False, rig=True)
        created.append(rigged.entry_id)
        assert rigged.outcome == Outcome.succeeded, rigged.error
        assert rigged.options.rig
        assert_rigged(vm.library.model_path(rigged.entry_id))

        ids = [e.entry_id for e in Library(vm.paths.library).entries()]
        assert all(entry_id in ids for entry_id in created)
    finally:
        for entry_id in created:
            vm.delete(entry_id)  # leave the user's library as it was
    stop_app(qtbot, vm)
