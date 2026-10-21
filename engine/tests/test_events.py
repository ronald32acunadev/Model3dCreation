import asyncio
from datetime import UTC, datetime

import pytest

from model3d_contracts.events import CompletedEvent, ProgressEvent, ResultStats, SnapshotEvent
from model3d_contracts.jobs import JobOptions, JobState
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.store import JobRecord, JobStore

TS = datetime(2026, 10, 20, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_record(tmp_path) -> JobRecord:
    return JobStore(tmp_path).create(JobOptions(), b"png", ".png")


def progress(job_id: str, p: float) -> ProgressEvent:
    return ProgressEvent(job_id=job_id, ts=TS, stage="shape", progress=p)


def completed(job_id: str) -> CompletedEvent:
    stats = ResultStats(vertices=1, faces=1, textured=False, duration_s=1.0)
    return CompletedEvent(job_id=job_id, ts=TS, result_url=f"/v1/jobs/{job_id}/result", stats=stats)


async def collect(agen, n: int = 100) -> list:
    items = []
    try:
        while len(items) < n:
            items.append(await asyncio.wait_for(agen.__anext__(), 1.0))
    except StopAsyncIteration:
        pass
    return items


@pytest.mark.anyio
async def test_subscriber_gets_snapshot_then_events(tmp_path):
    record = make_record(tmp_path)
    record.transition(JobState.shaping)
    bus = EventBus()
    agen = bus.subscribe(record)
    first = await agen.__anext__()
    assert isinstance(first, SnapshotEvent)
    assert (first.state, first.stage) == (JobState.shaping, "shape")
    bus.publish(progress(record.job_id, 0.5))
    bus.publish(completed(record.job_id))
    assert [e.type for e in await collect(agen)] == ["progress", "completed"]


@pytest.mark.anyio
async def test_late_subscriber_after_completion_gets_snapshot_and_final_then_stops(tmp_path):
    record = make_record(tmp_path)
    for state in (JobState.shaping, JobState.painting, JobState.succeeded):
        record.transition(state)
    bus = EventBus()
    bus.publish(completed(record.job_id))
    events = await collect(bus.subscribe(record))
    assert [e.type for e in events] == ["snapshot", "completed"]
    assert events[0].state == JobState.succeeded


@pytest.mark.anyio
async def test_events_are_isolated_per_job(tmp_path):
    a, b = make_record(tmp_path), make_record(tmp_path)
    bus = EventBus()
    agen_a, agen_b = bus.subscribe(a), bus.subscribe(b)
    await agen_a.__anext__()
    await agen_b.__anext__()
    bus.publish(progress(a.job_id, 0.5))
    bus.publish(progress(b.job_id, 0.3))
    bus.publish(completed(b.job_id))
    bus.publish(completed(a.job_id))
    for record, agen in ((a, agen_a), (b, agen_b)):
        events = await collect(agen)
        assert [e.type for e in events] == ["progress", "completed"]
        assert all(e.job_id == record.job_id for e in events)


@pytest.mark.anyio
async def test_two_subscribers_same_job_both_receive(tmp_path):
    record = make_record(tmp_path)
    bus = EventBus()
    first, second = bus.subscribe(record), bus.subscribe(record)
    await first.__anext__()
    await second.__anext__()
    bus.publish(progress(record.job_id, 0.5))
    bus.publish(completed(record.job_id))
    for agen in (first, second):
        assert [e.type for e in await collect(agen)] == ["progress", "completed"]


@pytest.mark.anyio
async def test_subscriber_is_unregistered_after_final(tmp_path):
    record = make_record(tmp_path)
    bus = EventBus()
    agen = bus.subscribe(record)
    await agen.__anext__()
    bus.publish(completed(record.job_id))
    await collect(agen)
    assert record.job_id not in bus._subscribers
