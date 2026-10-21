import json

import pytest
from model3d_contracts.events import CompletedEvent, ProgressEvent
from model3d_contracts.health import Health
from model3d_contracts.jobs import Job, JobOptions, Quality

from fake_engine import CLOSE, FakeEngine, error_body, free_port, make_job
from model3d_client.services.engine_api import ApiError, EngineApi


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


def call(qtbot, method, *args):
    """Run an EngineApi call and wait for its callback."""
    results = []
    method(*args, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    return results[0]


def test_health_parses_model(qtbot, engine):
    health = call(qtbot, EngineApi(engine.url).health)
    assert isinstance(health, Health)
    assert health.ready is True
    assert health.mode.name == "dual_gpu"


def test_health_none_when_unreachable(qtbot):
    assert call(qtbot, EngineApi(f"http://127.0.0.1:{free_port()}").health) is None


def test_submit_sends_multipart_and_returns_job(qtbot, engine, png):
    job = call(qtbot, EngineApi(engine.url).submit, png, JobOptions(texture=False, quality=Quality.draft))
    assert isinstance(job, Job)
    assert job.job_id == "J1" and job.seed == 42
    sent = engine.controller.submissions[0]
    assert json.loads(sent["options"]) == {"texture": False, "quality": "draft", "seed": None,
                                           "remove_background": True, "target_faces": None}
    assert sent["filename"] == "penguin.png"
    assert sent["image"] == png.read_bytes()
    assert engine.controller.origins == [None]


def test_submit_error_maps_error_body(qtbot, engine, png):
    engine.controller.submit = (503, error_body("engine_not_ready", "still loading"))
    error = call(qtbot, EngineApi(engine.url).submit, png, JobOptions())
    assert error == ApiError(status=503, code="engine_not_ready", message="still loading")


def test_submit_unreachable_is_engine_unreachable(qtbot, png):
    error = call(qtbot, EngineApi(f"http://127.0.0.1:{free_port()}").submit, png, JobOptions())
    assert isinstance(error, ApiError)
    assert (error.status, error.code) == (None, "engine_unreachable")


def test_cancel_and_job(qtbot, engine):
    api = EngineApi(engine.url)
    assert isinstance(call(qtbot, api.cancel, "J1"), Job)
    assert engine.controller.cancels == ["J1"]
    assert call(qtbot, api.job, "nope").code == "job_not_found"
    engine.controller.jobs["J1"] = make_job("J1")
    assert call(qtbot, api.job, "J1").job_id == "J1"


def test_events_stream_until_final_then_closed(qtbot, engine):
    engine.controller.push(
        {"type": "progress", "job_id": "J1", "ts": "2026-10-21T10:00:01Z", "stage": "shape", "progress": 0.5},
        {"type": "completed", "job_id": "J1", "ts": "2026-10-21T10:00:02Z", "result_url": "/v1/jobs/J1/result",
         "stats": {"vertices": 1, "faces": 2, "textured": True, "duration_s": 3.0}},
        CLOSE,
    )
    api = EngineApi(engine.url)  # the stream is a child of the api: keep it alive
    stream = api.open_events("J1")
    events, closed = [], []
    stream.event.connect(events.append)
    stream.closed.connect(lambda: closed.append(True))
    qtbot.waitUntil(lambda: bool(closed), timeout=5000)
    assert [type(e) for e in events] == [ProgressEvent, CompletedEvent]
    assert events[0].progress == 0.5
    assert closed == [True]
    assert engine.controller.origins == [None]


def test_events_closed_when_unreachable(qtbot):
    api = EngineApi(f"http://127.0.0.1:{free_port()}")
    stream = api.open_events("J1")
    closed = []
    stream.closed.connect(lambda: closed.append(True))
    qtbot.waitUntil(lambda: bool(closed), timeout=5000)
    assert closed == [True]


def test_download_result_writes_file_atomically(qtbot, engine, tmp_path):
    target = tmp_path / "entry" / "model.glb"
    target.parent.mkdir()
    assert call(qtbot, EngineApi(engine.url).download_result, "J1", target) == target
    assert target.read_bytes() == b"glTF fake model"
    assert not target.with_suffix(".part").exists()
