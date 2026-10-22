"""End-to-end acceptance on the real engine: needs both GPUs free and the weights in data/hf."""

import socket
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


def test_generates_penguin_end_to_end(qtbot):
    if engine_answers():
        pytest.skip("an engine already runs on 8765")
    _app, window = build_app([], viewer_factory=StubViewer)
    window.ask_close_anyway = lambda: True
    qtbot.addWidget(window)
    vm = window.vm

    vm.supervisor.start()
    qtbot.waitUntil(lambda: vm.supervisor.state == EngineState.ready, timeout=300_000)

    vm.generation.set_image(PENGUIN)
    vm.generation.set_texture(True)
    assert vm.generation.can_generate
    with qtbot.waitSignal(vm.generation.finished, timeout=600_000) as blocker:
        vm.generation.generate()
    entry = blocker.args[0]
    try:
        assert entry.outcome == Outcome.succeeded
        glb = vm.library.model_path(entry.entry_id)
        assert glb.read_bytes()[:4] == b"glTF"
        assert entry.entry_id in [e.entry_id for e in Library(vm.paths.library).entries()]
    finally:
        vm.delete(entry.entry_id)  # leave the user's library as it was

    done = []
    vm.shutdown(lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=60_000)
    qtbot.waitUntil(lambda: not engine_answers(), timeout=30_000)
