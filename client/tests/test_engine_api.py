import json

import pytest
from model3d_contracts.events import CompletedEvent, ProgressEvent
from model3d_contracts.health import Health
from model3d_contracts.jobs import Job, JobOptions, Quality

from fake_engine import CLOSE, FakeEngine, error_body, free_port, make_job
from model3d_client.services.engine_api import ApiError
from model3d_client.services.library import Library


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


def test_health_parses_model(qtbot, engine, make_api):
    health = call(qtbot, make_api(engine.url).health)
    assert isinstance(health, Health)
    assert health.ready is True
    assert health.mode.name == "dual_gpu"


def test_health_none_when_unreachable(qtbot, make_api):
    assert call(qtbot, make_api(f"http://127.0.0.1:{free_port()}").health) is None


def test_submit_sends_multipart_and_returns_job(qtbot, engine, png, make_api):
    job = call(qtbot, make_api(engine.url).submit, png, JobOptions(texture=False, quality=Quality.draft))
    assert isinstance(job, Job)
    assert job.job_id == "J1" and job.seed == 42
    sent = engine.controller.submissions[0]
    assert json.loads(sent["options"]) == {"texture": False, "quality": "draft", "seed": None,
                                           "remove_background": True, "target_faces": None, "rig": False, "detailed_face": False,
                                           "sharper_texture": False}
    assert sent["filename"] == "penguin.png"
    assert sent["image"] == png.read_bytes()
    assert engine.controller.origins == [None]


def test_submit_error_maps_error_body(qtbot, engine, png, make_api):
    engine.controller.submit = (503, error_body("engine_not_ready", "still loading"))
    error = call(qtbot, make_api(engine.url).submit, png, JobOptions())
    assert error == ApiError(status=503, code="engine_not_ready", message="still loading")


def test_submit_unreachable_is_engine_unreachable(qtbot, png, make_api):
    error = call(qtbot, make_api(f"http://127.0.0.1:{free_port()}").submit, png, JobOptions())
    assert isinstance(error, ApiError)
    assert (error.status, error.code) == (None, "engine_unreachable")


def test_cancel_and_job(qtbot, engine, make_api):
    api = make_api(engine.url)
    assert isinstance(call(qtbot, api.cancel, "J1"), Job)
    assert engine.controller.cancels == ["J1"]
    assert call(qtbot, api.job, "nope").code == "job_not_found"
    engine.controller.jobs["J1"] = make_job("J1")
    assert call(qtbot, api.job, "J1").job_id == "J1"


def test_events_stream_until_final_then_closed(qtbot, engine, make_api):
    engine.controller.push(
        {"type": "progress", "job_id": "J1", "ts": "2026-10-21T10:00:01Z", "stage": "shape", "progress": 0.5},
        {"type": "completed", "job_id": "J1", "ts": "2026-10-21T10:00:02Z", "result_url": "/v1/jobs/J1/result",
         "stats": {"vertices": 1, "faces": 2, "textured": True, "duration_s": 3.0}},
        CLOSE,
    )
    api = make_api(engine.url)  # the stream is a child of the api: keep it alive
    stream = api.open_events("J1")
    events, closed = [], []
    stream.event.connect(events.append)
    stream.closed.connect(lambda: closed.append(True))
    qtbot.waitUntil(lambda: bool(closed), timeout=5000)
    assert [type(e) for e in events] == [ProgressEvent, CompletedEvent]
    assert events[0].progress == 0.5
    assert closed == [True]
    assert engine.controller.origins == [None]


def test_events_closed_when_unreachable(qtbot, make_api):
    api = make_api(f"http://127.0.0.1:{free_port()}")
    stream = api.open_events("J1")
    closed = []
    stream.closed.connect(lambda: closed.append(True))
    qtbot.waitUntil(lambda: bool(closed), timeout=5000)
    assert closed == [True]


def test_download_result_writes_file_atomically(qtbot, engine, tmp_path, make_api):
    target = tmp_path / "entry" / "model.glb"
    target.parent.mkdir()
    assert call(qtbot, make_api(engine.url).download_result, "J1", target) == target
    assert target.read_bytes() == b"glTF fake model"
    assert not target.with_suffix(".part").exists()


def test_submit_rig_posts_model_part(qtbot, engine, tmp_path, make_api):
    model = tmp_path / "model.glb"
    model.write_bytes(b"glTF model bytes")
    job = call(qtbot, make_api(engine.url).submit_rig, model)
    assert isinstance(job, Job) and job.job_id == "J1"
    sent = engine.controller.rig_submissions[0]
    assert (sent["filename"], sent["content_type"], sent["model"]) == ("model.glb", "model/gltf-binary", b"glTF model bytes")


def test_download_result_original_variant(qtbot, engine, make_api, tmp_path):
    api = make_api(engine.url)
    target = tmp_path / "result.glb"
    engine.controller.original = b"glTF original"
    result = call(qtbot, lambda on_done: api.download_result("J1", target, on_done, variant="original"))
    assert result == target
    assert target.read_bytes() == b"glTF original"
    assert engine.controller.result_requests == ["original"]


def test_download_result_original_variant_missing(qtbot, engine, make_api, tmp_path):
    api = make_api(engine.url)
    target = tmp_path / "result.glb"
    engine.controller.original = None
    result = call(qtbot, lambda on_done: api.download_result("J1", target, on_done, variant="original"))
    assert isinstance(result, ApiError)
    assert result.status == 404
    assert result.code == "result_not_found"
    assert not target.exists()


def test_start_export_posts_the_format(qtbot, engine, make_api):
    api = make_api(engine.url)
    result = call(qtbot, api.start_export, "E1", "fbx")
    assert result is None
    assert engine.controller.export_posts == [("E1", "fbx")]
    engine.controller.export_start = (409, error_body("entry_busy", "busy"))
    result2 = call(qtbot, api.start_export, "E1", "fbx")
    assert isinstance(result2, ApiError)
    assert result2.status == 409
    assert result2.code == "entry_busy"
    assert result2.message == "busy"


def test_download_export_writes_the_file(qtbot, engine, make_api, tmp_path):
    api = make_api(engine.url)
    target = tmp_path / "out.fbx"
    engine.controller.export_file = b"fbx data"
    result = call(qtbot, api.download_export, "E1", "fbx", target)
    assert result == target
    assert target.read_bytes() == b"fbx data"
    assert engine.controller.export_gets == [("E1", "fbx")]


def test_download_export_not_ready_maps_the_error(qtbot, engine, make_api, tmp_path):
    api = make_api(engine.url)
    target = tmp_path / "out.fbx"
    engine.controller.export_answers = [(409, error_body("export_not_ready", "Export fbx is running"))]
    result = call(qtbot, api.download_export, "E1", "fbx", target)
    assert isinstance(result, ApiError)
    assert result.status == 409
    assert result.code == "export_not_ready"
    assert result.message == "Export fbx is running"
    assert not target.exists()


def test_download_export_zip_is_extracted_next_to_the_target(qtbot, engine, make_api, tmp_path):
    api = make_api(engine.url)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "robot.obj"
    engine.controller.export_zip = {
        "model.obj": b"mtllib model.mtl\no a\n",
        "model.mtl": b"newmtl a\n",
        "tex/a.png": b"png",
    }
    result = call(qtbot, api.download_export, "E1", "obj", target)
    assert result == target
    assert target.read_bytes() == b"mtllib robot.mtl\no a\n"
    assert (out_dir / "robot.mtl").read_bytes() == b"newmtl a\n"
    assert (out_dir / "tex" / "a.png").read_bytes() == b"png"
    assert not (out_dir / "model.obj").exists()
    assert not (out_dir / "model.mtl").exists()


def test_entry_reads_a_library_entry(qtbot, engine, make_api, tmp_path, png):
    api = make_api(engine.url)
    library = Library(tmp_path / "library")
    created = library.create(png, JobOptions())
    engine.controller.library_dir = library.root
    result = call(qtbot, api.entry, created.entry_id)
    assert result == created
    result2 = call(qtbot, api.entry, "nope")
    assert isinstance(result2, ApiError)
    assert result2.code == "entry_not_found"


def test_body_returns_the_detection(qtbot, engine, make_api):
    api = make_api(engine.url)
    result = call(qtbot, api.body, "E1")
    assert result == {"body": "biped", "legs": [], "arms": [], "up": 1}
    engine.controller.body_answer = (409, error_body("no_skeleton", "no skeleton"))
    result2 = call(qtbot, api.body, "E1")
    assert isinstance(result2, ApiError)
    assert result2.status == 409
    assert result2.code == "no_skeleton"
    assert result2.message == "no skeleton"


def test_start_walk_returns_the_new_entry(qtbot, engine, make_api, tmp_path, png):
    api = make_api(engine.url)
    library = Library(tmp_path / "library")
    source = library.create(png, JobOptions())

    def handler(entry_id, body):
        derived = library.create_derived(entry_id, suffix=" (walk)")
        return (202, json.loads(derived.model_dump_json()))

    engine.controller.walk = handler
    result = call(qtbot, api.start_walk, source.entry_id, "quadruped")
    assert result.name == source.name + " (walk)"
    assert result.source_entry_id == source.entry_id
    assert engine.controller.walk_posts == [(source.entry_id, "quadruped")]

    engine.controller.walk = None
    result2 = call(qtbot, api.start_walk, source.entry_id, "quadruped")
    assert isinstance(result2, ApiError)
    assert result2.code == "no_skeleton"
