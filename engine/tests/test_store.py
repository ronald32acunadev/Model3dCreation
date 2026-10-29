import json

import pytest

from model3d_contracts.jobs import JobError, JobOptions, JobState
from model3d_engine.jobs.store import InvalidTransition, JobStore, new_job_id

CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def make_store(tmp_path) -> JobStore:
    return JobStore(tmp_path)


def test_new_job_id_is_sortable_ulid():
    a = new_job_id(now_ms=1, rand=bytes(10))
    b = new_job_id(now_ms=2, rand=bytes(10))
    assert len(a) == len(b) == 26
    assert set(a) <= CROCKFORD and set(b) <= CROCKFORD
    assert a < b
    assert new_job_id(now_ms=0, rand=bytes(10)) == "0" * 26
    assert new_job_id() != new_job_id()


def test_happy_path_transitions(tmp_path):
    r = make_store(tmp_path).create(JobOptions(), b"png", ".png")
    r.transition(JobState.shaping)
    assert r.stage == "shape"
    r.progress = 0.7
    r.transition(JobState.painting)
    assert (r.stage, r.progress) == ("paint", 0.0)
    r.transition(JobState.succeeded)
    assert r.stage is None
    assert r.finished_at is not None and r.finished_at.tzinfo is not None


def test_shape_only_transition(tmp_path):
    r = make_store(tmp_path).create(JobOptions(), b"png", ".png")
    r.transition(JobState.shaping)
    r.transition(JobState.succeeded)
    assert r.state == JobState.succeeded


def test_failed_stores_error(tmp_path):
    r = make_store(tmp_path).create(JobOptions(), b"png", ".png")
    r.transition(JobState.shaping)
    r.transition(JobState.failed, error=JobError(code="gpu_out_of_memory", message="oom"))
    assert r.error.code == "gpu_out_of_memory"


@pytest.mark.parametrize(
    ("start_path", "target"),
    [
        ([], JobState.painting),
        ([JobState.shaping, JobState.succeeded], JobState.failed),
        ([JobState.cancelled], JobState.shaping),
        ([JobState.shaping, JobState.painting], JobState.shaping),
    ],
)
def test_invalid_transitions(tmp_path, start_path, target):
    r = make_store(tmp_path).create(JobOptions(), b"png", ".png")
    for state in start_path:
        r.transition(state)
    with pytest.raises(InvalidTransition):
        r.transition(target)


def test_create_writes_input_and_seed(tmp_path):
    store = make_store(tmp_path)
    r = store.create(JobOptions(), b"abc", ".jpg")
    assert (r.dir / "input.jpg").read_bytes() == b"abc"
    assert r.dir.parent == tmp_path
    assert r.dir.name == r.job_id
    assert 0 <= r.seed < 2**32
    assert r.state == JobState.queued
    assert store.create(JobOptions(seed=42), b"x", ".png").seed == 42
    assert store.get(r.job_id) is r
    assert store.get("missing") is None


def test_persist_writes_job_json(tmp_path):
    store = make_store(tmp_path)
    r = store.create(JobOptions(), b"png", ".png")
    store.persist(r)
    data = json.loads((r.dir / "job.json").read_text())
    assert data["state"] == "queued"
    assert data["job_id"] == r.job_id
    assert data["links"]["result"] == f"/v1/jobs/{r.job_id}/result"
    assert data["created_at"].endswith("Z")


def test_rigging_transitions(tmp_path):
    store = make_store(tmp_path)
    for path in ([JobState.shaping], [JobState.shaping, JobState.painting], []):
        record = store.create(JobOptions(rig=True), b"png", ".png")
        for state in path:
            record.transition(state)
        record.transition(JobState.rigging)
        assert (record.state, record.stage, record.progress) == (JobState.rigging, "rig", 0.0)
        record.transition(JobState.succeeded)
        assert record.stage is None
    record = store.create(JobOptions(rig=True), b"png", ".png")
    record.transition(JobState.shaping)
    record.transition(JobState.rigging)
    with pytest.raises(InvalidTransition):
        record.transition(JobState.painting)


def test_create_rig_writes_input_and_options(tmp_path):
    record = make_store(tmp_path).create_rig(b"glTF-bytes", textured=True)
    assert (record.dir / "rig_input.glb").read_bytes() == b"glTF-bytes"
    assert record.options == JobOptions(rig=True, texture=True)
    assert record.state == JobState.queued
