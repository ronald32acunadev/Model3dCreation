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
from model3d_contracts.jobs import FINAL_STATES, JobError, JobOptions, JobState, Quality

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
