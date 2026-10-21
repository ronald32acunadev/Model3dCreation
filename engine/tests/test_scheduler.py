import asyncio

import pytest

from model3d_contracts.jobs import JobOptions, JobState
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.protocol import StageError

from .fakes import FakeRunner

pytestmark = pytest.mark.anyio


async def wait_final(record, timeout: float = 5.0) -> None:
    async def poll():
        while not record.is_final:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def make(tmp_path, shape=None, paint=None):
    store, bus = JobStore(tmp_path), EventBus()
    shape = shape or FakeRunner("shape.glb")
    paint = paint or FakeRunner("model.glb")
    scheduler = Scheduler(store, bus, shape, paint)
    await scheduler.start()
    return scheduler, store, bus, shape, paint


async def submit(scheduler, store, bus, options=None):
    record = store.create(options or JobOptions(), b"png", ".png")
    events = bus.subscribe(record)
    await events.__anext__()  # snapshot
    scheduler.submit(record)
    return record, events


async def drain(events) -> list:
    return [event async for event in events]


async def test_textured_job_runs_both_stages(tmp_path):
    scheduler, store, bus, _, _ = await make(tmp_path)
    record, events = await submit(scheduler, store, bus)
    await wait_final(record)
    received = await drain(events)
    await scheduler.stop()

    assert record.state == JobState.succeeded
    assert (record.dir / "model.glb").exists()
    assert received[-1].type == "completed"
    assert {e.type for e in received[:-1]} == {"progress"}
    assert {e.stage for e in received[:-1]} == {"shape", "paint"}
    assert received[-1].stats.textured is True
    assert received[-1].stats.faces == 12


async def test_shape_only_job_skips_paint(tmp_path):
    scheduler, store, bus, _, paint = await make(tmp_path)
    record, events = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(record)
    received = await drain(events)
    await scheduler.stop()

    assert record.state == JobState.succeeded
    assert (record.dir / "model.glb").exists()
    assert not (record.dir / "shape.glb").exists()
    assert paint.runs == []
    assert received[-1].stats.textured is False


async def test_jobs_pipeline_across_stages(tmp_path):
    shape, paint = FakeRunner("shape.glb", delay=0.2), FakeRunner("model.glb", delay=0.2)
    scheduler, store, bus, _, _ = await make(tmp_path, shape, paint)
    first, first_events = await submit(scheduler, store, bus)
    second, second_events = await submit(scheduler, store, bus)
    await wait_final(first)
    await wait_final(second)
    first_received, second_received = await drain(first_events), await drain(second_events)
    await scheduler.stop()

    second_shape_start = next(start for job, start, _ in shape.runs if job == second.job_id)
    first_paint_end = next(end for job, _, end in paint.runs if job == first.job_id)
    assert second_shape_start < first_paint_end
    assert all(e.job_id == first.job_id for e in first_received)
    assert all(e.job_id == second.job_id for e in second_received)


async def test_stage_error_fails_job(tmp_path):
    paint = FakeRunner("model.glb", fail_with=StageError("gpu_out_of_memory", "Paint ran out of VRAM"))
    scheduler, store, bus, _, _ = await make(tmp_path, paint=paint)
    record, events = await submit(scheduler, store, bus)
    await wait_final(record)
    received = await drain(events)
    paint.fail_with = None
    after, _ = await submit(scheduler, store, bus)
    await wait_final(after)
    await scheduler.stop()

    assert record.state == JobState.failed
    assert record.error.code == "gpu_out_of_memory"
    assert received[-1].type == "failed"
    assert after.state == JobState.succeeded


async def test_unexpected_error_is_internal_error(tmp_path):
    shape = FakeRunner("shape.glb")

    async def boom(task, on_progress):
        raise RuntimeError("disk full")

    shape.run = boom
    scheduler, store, bus, _, _ = await make(tmp_path, shape=shape)
    record, _ = await submit(scheduler, store, bus)
    await wait_final(record)
    await scheduler.stop()

    assert record.error.code == "internal_error"
    assert "disk full" in record.error.message


async def test_cancel_while_shaping_stops_before_paint(tmp_path):
    shape = FakeRunner("shape.glb", delay=0.2)
    scheduler, store, bus, _, paint = await make(tmp_path, shape=shape)
    record, events = await submit(scheduler, store, bus)
    while record.state != JobState.shaping:
        await asyncio.sleep(0.01)
    assert scheduler.cancel(record.job_id) is True
    await wait_final(record)
    received = await drain(events)
    await scheduler.stop()

    assert record.state == JobState.cancelled
    assert paint.runs == []
    assert received[-1].type == "cancelled"


async def test_cancel_queued_job_is_immediate(tmp_path):
    shape = FakeRunner("shape.glb", delay=0.3)
    scheduler, store, bus, _, _ = await make(tmp_path, shape=shape)
    await submit(scheduler, store, bus)
    queued, events = await submit(scheduler, store, bus)
    await asyncio.sleep(0.05)
    assert scheduler.queue_position(queued.job_id) == 0
    assert scheduler.cancel(queued.job_id) is True
    assert queued.state == JobState.cancelled
    assert scheduler.queue_position(queued.job_id) is None
    assert [e.type for e in await drain(events)] == ["cancelled"]
    await scheduler.stop()


async def test_cancel_finished_job_returns_false(tmp_path):
    scheduler, store, bus, _, _ = await make(tmp_path)
    record, _ = await submit(scheduler, store, bus)
    await wait_final(record)
    assert scheduler.cancel(record.job_id) is False
    assert scheduler.cancel("missing") is False
    assert record.state == JobState.succeeded
    await scheduler.stop()


async def test_ready_follows_runners(tmp_path):
    store, bus = JobStore(tmp_path), EventBus()
    scheduler = Scheduler(store, bus, FakeRunner("shape.glb"), FakeRunner("model.glb"))
    assert scheduler.ready is False
    await scheduler.start()
    assert scheduler.ready is True
    await scheduler.stop()
