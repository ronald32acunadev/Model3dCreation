import json
import threading

import pytest

from fake_engine import FakeEngine, job
from model3d import jobs, library
from model3d.engine_client import EngineClient, EngineUnreachable
from model3d.tracker import ENGINE_DOWN_MESSAGE

PNG = b"\x89PNG\r\n\x1a\nfake"
OPTIONS = {"texture": True, "quality": "final", "rig": True}


@pytest.fixture
def engine():
    with FakeEngine() as fake:
        yield fake


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "inputs" / "cat.png"
    path.parent.mkdir()
    path.write_bytes(PNG)
    return path


def entries(root):
    return library.list_entries(root)


def test_generation_downloads_records_entry_and_queues_import(engine, image, tmp_path):
    root = tmp_path / "library"
    engine.controller.jobs = [job(state="rigging", stage="rig", progress=0.5), job(state="succeeded")]
    status = jobs.JobStatus()
    jobs.run_generation(EngineClient(engine.url), root, image, OPTIONS, status, poll_s=0)
    [entry] = entries(root)
    assert (entry["outcome"], entry["job_id"], entry["seed"]) == ("succeeded", "J1", 42)
    assert entry["options"] == OPTIONS
    assert library.model_path(root, entry["entry_id"]).read_bytes() == b"glTF result"
    assert status.pending_import == library.model_path(root, entry["entry_id"])
    assert status.busy is False and status.error is None
    sent = engine.controller.received[0]["parts"]
    assert json.loads(sent["options"]["data"]) == OPTIONS


def test_generation_with_engine_down_creates_no_entry(image, tmp_path):
    status = jobs.JobStatus()
    jobs.run_generation(EngineClient("http://127.0.0.1:9", timeout=1), tmp_path, image, OPTIONS, status, poll_s=0)
    assert status.error == ("engine_unreachable", ENGINE_DOWN_MESSAGE)
    assert entries(tmp_path) == []


def test_generation_while_engine_loads(engine, image, tmp_path):
    engine.controller.health = {"ready": False, "rig": True, "rig_reason": None}
    status = jobs.JobStatus()
    jobs.run_generation(EngineClient(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    assert status.error == ("engine_not_ready", "The engine is still loading")


def test_rig_unavailable_reports_the_reason(engine, image, tmp_path):
    engine.controller.health = {"ready": True, "rig": False, "rig_reason": "UniRig weights are missing"}
    status = jobs.JobStatus()
    jobs.run_generation(EngineClient(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    assert status.error == ("rig_unavailable", "UniRig weights are missing")
    assert engine.controller.received == []


class LosesEngine(EngineClient):
    """Answers the submission, then the engine disappears (the desktop app was closed)."""

    def job(self, job_id):
        raise EngineUnreachable("connection refused")


def test_lost_engine_marks_entry_failed_and_asks_for_the_app(engine, image, tmp_path):
    status = jobs.JobStatus()
    jobs.run_generation(LosesEngine(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    [entry] = entries(tmp_path)
    assert entry["outcome"] == "failed" and entry["error"]["code"] == "engine_lost"
    assert status.error == ("engine_lost", ENGINE_DOWN_MESSAGE)
    assert status.busy is False


def test_failed_job_records_its_error(engine, image, tmp_path):
    engine.controller.jobs = [job(state="failed", error={"code": "rig_failed", "message": "UniRig failed"})]
    status = jobs.JobStatus()
    jobs.run_generation(EngineClient(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    [entry] = entries(tmp_path)
    assert entry["error"] == {"code": "rig_failed", "message": "UniRig failed"}
    assert status.error == ("rig_failed", "UniRig failed")
    assert status.pending_import is None


def test_cancel_sends_delete_and_records_cancelled(engine, image, tmp_path):
    engine.controller.jobs = [job(state="shaping", stage="shape"), job(state="cancelled")]
    status = jobs.JobStatus()
    status.cancel_requested = True
    jobs.run_generation(EngineClient(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    assert {"path": "/v1/jobs/J1", "method": "DELETE"} in engine.controller.received
    assert entries(tmp_path)[0]["outcome"] == "cancelled"


def test_add_skeleton_writes_new_entry_and_keeps_source(engine, image, tmp_path):
    source = library.create_entry(tmp_path, image, {"texture": True, "quality": "high"})
    source = {**source, "outcome": "succeeded"}
    library.update_entry(tmp_path, source)
    library.model_path(tmp_path, source["entry_id"]).write_bytes(b"glTF source")
    before = (tmp_path / source["entry_id"] / "entry.json").read_bytes()
    engine.controller.jobs = [job(state="succeeded")]
    status = jobs.JobStatus()
    jobs.run_add_skeleton(EngineClient(engine.url), tmp_path, source, status, poll_s=0)
    rigged = next(entry for entry in entries(tmp_path) if entry["entry_id"] != source["entry_id"])
    assert rigged["name"] == "cat (rigged)" and rigged["source_entry_id"] == source["entry_id"]
    assert rigged["options"] == {"texture": True, "quality": "high", "rig": True}
    assert rigged["outcome"] == "succeeded"
    assert engine.controller.received[0]["path"] == "/v1/rig-jobs"
    assert engine.controller.received[0]["parts"]["model"]["data"] == b"glTF source"
    assert (tmp_path / source["entry_id"] / "entry.json").read_bytes() == before
    assert status.pending_import == library.model_path(tmp_path, rigged["entry_id"])


def test_status_snapshot_is_thread_safe():
    status = jobs.JobStatus()
    threads = [threading.Thread(target=status.update, kwargs={"stage": "rig", "progress": i / 100}) for i in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert status.snapshot()["stage"] == "rig"


class EngineRestarted(EngineClient):
    """The desktop app restarted the engine: it no longer knows the job."""

    def job(self, job_id):
        from model3d.engine_client import EngineError

        raise EngineError("job_not_found", f"No job {job_id}")


def test_job_not_found_while_polling_counts_as_engine_lost(engine, image, tmp_path):
    status = jobs.JobStatus()
    jobs.run_generation(EngineRestarted(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    [entry] = entries(tmp_path)
    assert entry["outcome"] == "failed" and entry["error"]["code"] == "engine_lost"
    assert status.error == ("engine_lost", ENGINE_DOWN_MESSAGE)


class BrokenAnswers(EngineClient):
    def job(self, job_id):
        raise ValueError("Expecting value: line 1 column 1")


def test_unexpected_error_fails_entry_and_reports_it(engine, image, tmp_path):
    status = jobs.JobStatus()
    jobs.run_generation(BrokenAnswers(engine.url), tmp_path, image, OPTIONS, status, poll_s=0)
    [entry] = entries(tmp_path)
    assert entry["outcome"] == "failed" and entry["error"]["code"] == "internal_error"
    assert status.error[0] == "internal_error" and "Expecting value" in status.error[1]
    assert status.busy is False


def test_unreadable_image_reports_an_error(engine, tmp_path):
    status = jobs.JobStatus()
    jobs.run_generation(EngineClient(engine.url), tmp_path, tmp_path / "missing.png", OPTIONS, status, poll_s=0)
    assert status.error is not None and status.error[0] == "internal_error"
    assert status.busy is False
