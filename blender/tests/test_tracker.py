import socket

from fake_engine import FakeEngine, job
from model3d.engine_client import EngineClient
from model3d.tracker import ENGINE_DOWN_MESSAGE, JobTracker


def test_tracker_reports_progress_then_success():
    with FakeEngine() as engine:
        engine.controller.jobs = [
            job(state="shaping", stage="shape", progress=0.3),
            job(state="rigging", stage="rig", progress=0.45),
            job(state="succeeded"),
        ]
        tracker = JobTracker(EngineClient(engine.url), "J1")
        first, second, third = tracker.poll(), tracker.poll(), tracker.poll()
    assert (first.state, first.stage, first.progress) == ("shaping", "shape", 0.3)
    assert (second.state, second.stage) == ("rigging", "rig")
    assert third.state == "succeeded" and third.done and not third.lost


def test_tracker_reports_failure_with_error():
    with FakeEngine() as engine:
        engine.controller.jobs = [job(state="failed", error={"code": "rig_failed", "message": "UniRig failed"})]
        status = JobTracker(EngineClient(engine.url), "J1").poll()
    assert status.done and status.error == {"code": "rig_failed", "message": "UniRig failed"}


def test_tracker_marks_lost_after_three_unreachable_polls():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tracker = JobTracker(EngineClient(f"http://127.0.0.1:{port}", timeout=1), "J1")
    assert tracker.poll().lost is False
    assert tracker.poll().lost is False
    lost = tracker.poll()
    assert lost.lost is True and lost.done is True
    assert ENGINE_DOWN_MESSAGE == "Open the Model3D desktop app"
