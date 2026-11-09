from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from model3d_contracts.events import (
    CancelledEvent,
    CompletedEvent,
    FailedEvent,
    ProgressEvent,
    ResultStats,
    SnapshotEvent,
    parse_event,
)
from model3d_contracts.jobs import FINAL_STATES, Job, JobError, JobOptions, JobState, Quality

TS = datetime(2026, 10, 20, 19, 5, tzinfo=UTC)


def test_options_defaults():
    options = JobOptions()
    assert options.texture is True
    assert options.quality == Quality.final
    assert options.quality == "final"
    assert options.seed is None
    assert options.remove_background is True
    assert options.target_faces is None


def test_options_rejects_small_target_faces():
    with pytest.raises(ValidationError) as exc:
        JobOptions(target_faces=999)
    assert "target_faces must be at least 1000" in str(exc.value)

    assert JobOptions(target_faces=1000).target_faces == 1000


def test_options_rejects_unknown_field():
    with pytest.raises(ValidationError):
        JobOptions.model_validate({"colour": 1})


def test_options_rejects_out_of_range_seed():
    with pytest.raises(ValidationError):
        JobOptions(seed=-1)
    with pytest.raises(ValidationError):
        JobOptions(seed=2**32)

    assert JobOptions(seed=2**32 - 1).seed == 2**32 - 1


@pytest.mark.parametrize(
    "event",
    [
        SnapshotEvent(job_id="01JB", ts=TS, state=JobState.shaping, stage="shape", progress=0.35),
        ProgressEvent(job_id="01JB", ts=TS, stage="paint", progress=0.62, message="Baking view 4 of 6"),
        CompletedEvent(
            job_id="01JB",
            ts=TS,
            result_url="/v1/jobs/01JB/result",
            stats=ResultStats(vertices=21034, faces=40000, textured=True, duration_s=74.2),
        ),
        FailedEvent(job_id="01JB", ts=TS, error=JobError(code="gpu_out_of_memory", message="Paint stage ran out of VRAM")),
        CancelledEvent(job_id="01JB", ts=TS),
    ],
)
def test_event_round_trip(event):
    text = event.model_dump_json()
    parsed = parse_event(text)
    assert parsed == event
    assert type(parsed) is type(event)
    assert '"ts":"2026-10-20T19:05:00Z"' in text


def test_parse_event_accepts_bytes():
    data = CancelledEvent(job_id="x", ts=TS).model_dump_json().encode()
    assert isinstance(parse_event(data), CancelledEvent)


def test_parse_event_rejects_unknown_type():
    with pytest.raises(ValidationError):
        parse_event('{"type":"bogus","job_id":"x","ts":"2026-10-20T19:05:00Z"}')


def test_final_states():
    assert FINAL_STATES == {JobState.succeeded, JobState.failed, JobState.cancelled}


def test_quality_has_a_high_detail_level():
    assert [q.value for q in Quality] == ["draft", "final", "high"]
    assert JobOptions.model_validate({"quality": "high"}).quality == Quality.high


def test_job_options_rig_defaults_false():
    assert JobOptions().rig is False
    assert JobOptions.model_validate({"rig": True}).rig is True


def test_rigging_state_and_rig_stage():
    assert JobState("rigging") == JobState.rigging
    job = Job.model_validate(
        {
            "job_id": "x",
            "state": "rigging",
            "stage": "rig",
            "seed": 1,
            "created_at": "2026-10-22T19:05:00Z",
            "links": {"self": "/v1/jobs/x", "events": "/v1/jobs/x/events", "result": "/v1/jobs/x/result"},
        }
    )
    assert job.state == JobState.rigging
    assert job.stage == "rig"


def test_detailed_face_needs_rig():
    with pytest.raises(ValidationError) as excinfo:
        JobOptions(detailed_face=True)
    assert "detailed_face needs rig: true" in str(excinfo.value)
    assert JobOptions(detailed_face=True, rig=True).detailed_face is True
    assert JobOptions().detailed_face is False


def test_detailing_state_is_not_final():
    assert JobState.detailing.value == "detailing"
    assert JobState.detailing not in FINAL_STATES


def test_job_notice_defaults_to_none():
    from model3d_contracts.jobs import JobLinks

    job = Job(job_id="j1", state=JobState.queued, seed=1, created_at=TS, links=JobLinks(self="/a", events="/b", result="/c"))
    assert job.notice is None
    job2 = Job(job_id="j1", state=JobState.queued, seed=1, created_at=TS, links=JobLinks(self="/a", events="/b", result="/c"), notice="Detailed face was cancelled")
    assert job2.notice == "Detailed face was cancelled"
