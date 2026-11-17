import time

from fastapi.testclient import TestClient

from model3d_contracts.jobs import JobOptions, JobState
from model3d_contracts.library import Entry, Library, Outcome
from model3d_contracts.events import ResultStats
from model3d_engine.api.app import create_app
from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobRecord, JobStore

from .fakes import FakeRunner
from .test_api import assert_error, image_bytes, MODE


def make(tmp_path) -> tuple[TestClient, JobStore, Library]:
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, FakeRunner("shape.glb"), FakeRunner("model.glb"))
    app = create_app(
        settings, scheduler, store, bus,
        health=lambda: {"ready": scheduler.ready, "mode": MODE, "gpus": [], "rig": False, "rig_reason": None},
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    lib = Library(tmp_path / "library", recover=False)
    return client, store, lib


def add_entry(lib, tmp_path, name) -> Entry:
    img = tmp_path / f"{name}.png"
    img.write_bytes(image_bytes())
    entry = lib.create(img, JobOptions(), name=name)
    lib.model_path(entry.entry_id).write_bytes(b"glb-" + name.encode())
    entry = entry.model_copy(update={"outcome": Outcome.succeeded})
    lib.update(entry)
    return entry


def finished_job(store) -> JobRecord:
    record = store.create(JobOptions(), image_bytes(), ".png")
    (record.dir / "model.glb").write_bytes(b"model-bytes")
    record.state = JobState.succeeded
    return record


def test_list_returns_entries_newest_first_with_files(tmp_path):
    client, store, lib = make(tmp_path)
    add_entry(lib, tmp_path, "a")
    time.sleep(1.1)
    add_entry(lib, tmp_path, "b")
    resp = client.get("/v1/library")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["name"] for e in body] == ["b", "a"]
    assert body[0]["files"] == ["input.png", "model.glb"]


def test_get_unknown_and_malformed_entries_are_not_found(tmp_path):
    client, store, lib = make(tmp_path)
    assert_error(client.get("/v1/library/nope"), 404, "entry_not_found")
    # "..%2F" decodes to "../": the path leaves /v1/library and matches no route.
    assert client.get("/v1/library/..%2Fjobs").status_code == 404
    assert_error(client.get("/v1/library/-x"), 404, "entry_not_found")


def test_download_file_returns_its_bytes(tmp_path):
    client, store, lib = make(tmp_path)
    entry = add_entry(lib, tmp_path, "a")
    resp = client.get(f"/v1/library/{entry.entry_id}/files/model.glb")
    assert resp.status_code == 200
    assert resp.content == b"glb-a"


def test_file_outside_the_entry_is_not_found(tmp_path):
    client, store, lib = make(tmp_path)
    entry = add_entry(lib, tmp_path, "a")
    assert_error(client.get(f"/v1/library/{entry.entry_id}/files/entry.json"), 404, "file_not_found")
    escaped = client.get(f"/v1/library/{entry.entry_id}/files/..%2Fentry.json")
    assert escaped.status_code == 404
    assert b"entry_id" not in escaped.content
    assert_error(client.get(f"/v1/library/{entry.entry_id}/files/missing.glb"), 404, "file_not_found")


def test_save_finished_job(tmp_path):
    client, store, lib = make(tmp_path)
    record = finished_job(store)
    (record.dir / "stats.json").write_text(ResultStats(vertices=8, faces=12, textured=True, duration_s=3.5).model_dump_json())
    resp = client.post("/v1/library", json={"job_id": record.job_id, "name": "  Dog  "})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Dog"
    assert body["outcome"] == "succeeded"
    assert body["job_id"] == record.job_id
    assert body["seed"] == record.seed
    assert body["stats"] == {"vertices": 8, "faces": 12, "textured": True, "duration_s": 3.5}
    assert lib.model_path(body["entry_id"]).read_bytes() == b"model-bytes"
    listing = client.get("/v1/library").json()
    assert [e["entry_id"] for e in listing] == [body["entry_id"]]


def test_save_without_name_uses_the_job_id_and_no_stats(tmp_path):
    client, store, lib = make(tmp_path)
    record = finished_job(store)
    resp = client.post("/v1/library", json={"job_id": record.job_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == record.job_id
    assert body["stats"] is None


def test_save_unknown_job_is_not_found(tmp_path):
    client, store, lib = make(tmp_path)
    assert_error(client.post("/v1/library", json={"job_id": "nope"}), 404, "job_not_found")
    assert lib.entries() == []


def test_save_unfinished_job_is_a_conflict(tmp_path):
    client, store, lib = make(tmp_path)
    record = store.create(JobOptions(), image_bytes(), ".png")
    assert_error(client.post("/v1/library", json={"job_id": record.job_id}), 409, "job_not_finished")
    assert lib.entries() == []


def test_save_job_without_image_is_a_conflict(tmp_path):
    client, store, lib = make(tmp_path)
    record = store.create_rig(b"glb", False)
    (record.dir / "model.glb").write_bytes(b"glb")
    record.state = JobState.succeeded
    assert_error(client.post("/v1/library", json={"job_id": record.job_id}), 409, "job_has_no_image")
    assert lib.entries() == []
