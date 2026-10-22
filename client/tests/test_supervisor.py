import sys
from pathlib import Path

import pytest

from fake_engine import FakeEngine, free_port
from model3d_client.paths import AppPaths
from model3d_client.services.engine_api import EngineApi
from model3d_client.services.supervisor import EngineState, EngineSupervisor

FAKE_PROCESS = str(Path(__file__).parent / "fake_engine_process.py")
NEVER_SPAWN = [sys.executable, "-c", "raise SystemExit(3)"]


@pytest.fixture
def paths(tmp_path):
    return AppPaths(repo=tmp_path, data=tmp_path / "data")


@pytest.fixture
def make_supervisor(qtbot, make_api, paths):
    """Supervisor factory; teardown stops polling (and any owned engine) before objects are freed."""
    made = []

    def make(port, program, **kwargs):
        made.append(EngineSupervisor(make_api(f"http://127.0.0.1:{port}"), paths, program=program, poll_ms=50, **kwargs))
        return made[-1]

    yield make
    for sup in made:
        stop(qtbot, sup)


def stop(qtbot, sup):
    stopped = []
    sup.stop(lambda: stopped.append(True))
    qtbot.waitUntil(lambda: bool(stopped), timeout=20000)


def answers(qtbot, port) -> bool:
    result = []
    api = EngineApi(f"http://127.0.0.1:{port}")
    api.health(result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=5000)
    qtbot.wait(50)  # the reply's deleteLater runs before the api goes away
    return result[0] is not None


def test_external_engine_is_used_and_not_owned(qtbot, paths, make_supervisor):
    engine = FakeEngine().start()
    try:
        sup = make_supervisor(engine.port, NEVER_SPAWN)
        sup.start()
        qtbot.waitUntil(lambda: sup.state == EngineState.ready, timeout=5000)
        assert sup.owned is False
        assert sup.health.mode.name == "dual_gpu"
    finally:
        engine.stop()


def test_spawns_when_nothing_answers_and_reaches_ready(qtbot, paths, make_supervisor):
    port = free_port()
    sup = make_supervisor(port, [sys.executable, FAKE_PROCESS, str(port), "ready"])
    states = []
    sup.state_changed.connect(states.append)
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.ready, timeout=15000)
    assert sup.owned is True
    assert states[0] == EngineState.starting
    assert "fake engine on" in paths.engine_log.read_text()
    stop(qtbot, sup)


def test_engine_gets_model3d_home(qtbot, paths, make_supervisor):
    port = free_port()
    program = [sys.executable, "-c", "import os, sys; print('HOME=' + os.environ['MODEL3D_HOME']); sys.exit(1)"]
    sup = make_supervisor(port, program)
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.crashed, timeout=15000)
    assert f"HOME={paths.data}" in paths.engine_log.read_text()


def test_loading_until_ready(qtbot, paths, make_supervisor):
    port = free_port()
    sup = make_supervisor(port, [sys.executable, FAKE_PROCESS, str(port), "loading"])
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.loading, timeout=15000)
    stop(qtbot, sup)


def test_health_error_sets_error_state(qtbot, paths, make_supervisor):
    port = free_port()
    sup = make_supervisor(port, [sys.executable, FAKE_PROCESS, str(port), "error"])
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.error, timeout=15000)
    assert sup.health.error.code == "worker_start_failed"
    stop(qtbot, sup)


def test_owned_crash_sets_crashed(qtbot, paths, make_supervisor):
    sup = make_supervisor(free_port(), [sys.executable, "-c", "raise SystemExit(1)"])
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.crashed, timeout=15000)
    assert "1" in sup.failure


def test_start_timeout_kills_and_sets_error(qtbot, paths, make_supervisor):
    sup = make_supervisor(free_port(), [sys.executable, "-c", "import time; time.sleep(60)"], start_timeout_s=0.5)
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.error, timeout=15000)
    assert "did not answer" in sup.failure


def test_stop_terminates_owned_engine(qtbot, paths, make_supervisor):
    port = free_port()
    sup = make_supervisor(port, [sys.executable, FAKE_PROCESS, str(port), "ready"])
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.ready, timeout=15000)
    stop(qtbot, sup)
    assert sup.state == EngineState.stopped
    assert not answers(qtbot, port)


def test_stop_leaves_external_engine_running(qtbot, paths, make_supervisor):
    engine = FakeEngine().start()
    try:
        sup = make_supervisor(engine.port, NEVER_SPAWN)
        sup.start()
        qtbot.waitUntil(lambda: sup.state == EngineState.ready, timeout=5000)
        stop(qtbot, sup)
        assert answers(qtbot, engine.port)
    finally:
        engine.stop()


def test_owned_exit_while_other_engine_answers_adopts_external(qtbot, paths, make_supervisor):
    # Another engine was still detecting GPUs (port closed) when the app spawned its own;
    # ours exits because the port is taken, and the other one answers from then on.
    port = free_port()
    sup = make_supervisor(port, [sys.executable, "-c", "import time; time.sleep(0.5); raise SystemExit(1)"])
    sup.start()
    qtbot.waitUntil(lambda: sup.state == EngineState.starting, timeout=5000)
    other = FakeEngine(port).start()
    try:
        # Ready may show first (the other engine answers while ours still starts); once ours
        # exits, the app must keep the other one as external instead of reporting a crash.
        qtbot.waitUntil(lambda: sup.state == EngineState.ready and not sup.owned, timeout=15000)
        assert sup.failure is None
    finally:
        other.stop()
