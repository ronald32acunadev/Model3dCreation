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


SHAPE, PAINT = "shape.glb", "model.glb"


async def make_swap(tmp_path, shape_delay=0.05, paint_delay=0.05, paint_start_error=None):
    log: list[tuple[str, str]] = []
    shape = FakeRunner(SHAPE, delay=shape_delay, log=log)
    paint = FakeRunner(PAINT, delay=paint_delay, log=log, start_error=paint_start_error)
    store, bus = JobStore(tmp_path), EventBus()
    scheduler = Scheduler(store, bus, shape, paint, swap=True)
    await scheduler.start()
    scheduler.runners = (shape, paint)  # for tests that compare run times
    return scheduler, store, bus, log


def never_both_loaded(log) -> bool:
    loaded = set()
    for action, name in log:
        if action == "load":
            loaded.add(name)
            if len(loaded) > 1:
                return False
        else:
            loaded.discard(name)
    return True


async def test_swap_batches_by_stage(tmp_path):
    scheduler, store, bus, log = await make_swap(tmp_path)
    assert scheduler.ready is True
    records = [(await submit(scheduler, store, bus))[0] for _ in range(3)]
    for record in records:
        await wait_final(record)
    await scheduler.stop()
    assert [record.state for record in records] == [JobState.succeeded] * 3
    assert log == [("load", SHAPE), ("unload", SHAPE), ("load", PAINT)]
    assert never_both_loaded(log)


async def test_swap_back_when_new_shape_work_arrives(tmp_path):
    scheduler, store, bus, log = await make_swap(tmp_path, paint_delay=0.3)
    first, _ = await submit(scheduler, store, bus)
    while first.state != JobState.painting:
        await asyncio.sleep(0.01)
    second, events = await submit(scheduler, store, bus)
    await wait_final(second)
    received = await drain(events)
    await scheduler.stop()
    assert second.state == JobState.succeeded
    assert received[-1].stats.textured is True
    shape, paint = scheduler.runners
    first_paint_end = next(end for job_id, _, end in paint.runs if job_id == first.job_id)
    second_shape_start = next(start for job_id, start, _ in shape.runs if job_id == second.job_id)
    assert second_shape_start >= first_paint_end  # it waited for the paint batch
    assert log == [
        ("load", SHAPE), ("unload", SHAPE), ("load", PAINT), ("unload", PAINT),
        ("load", SHAPE), ("unload", SHAPE), ("load", PAINT),
    ]
    assert never_both_loaded(log)


async def test_swap_untextured_jobs_never_load_paint(tmp_path):
    scheduler, store, bus, log = await make_swap(tmp_path)
    records = [(await submit(scheduler, store, bus, JobOptions(texture=False)))[0] for _ in range(2)]
    for record in records:
        await wait_final(record)
    await scheduler.stop()
    assert [record.state for record in records] == [JobState.succeeded] * 2
    assert log == [("load", SHAPE)]


async def test_swap_cancel_while_waiting_for_paint(tmp_path):
    scheduler, store, bus, log = await make_swap(tmp_path, shape_delay=0.2)
    record, _ = await submit(scheduler, store, bus)
    while record.state != JobState.shaping:
        await asyncio.sleep(0.01)
    assert scheduler.cancel(record.job_id) is True
    await wait_final(record)
    await asyncio.sleep(0.1)
    await scheduler.stop()
    assert record.state == JobState.cancelled
    assert ("load", PAINT) not in log


async def test_swap_paint_load_failure_fails_waiting_jobs(tmp_path):
    error = StageError("worker_start_failed", "paint worker exited with code 1 while loading")
    scheduler, store, bus, log = await make_swap(tmp_path, paint_start_error=error)
    textured, _ = await submit(scheduler, store, bus)
    await wait_final(textured)
    untextured, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(untextured)
    await scheduler.stop()
    assert textured.state == JobState.failed
    assert textured.error.code == "worker_unavailable"
    assert untextured.state == JobState.succeeded
    assert log == [("load", SHAPE), ("unload", SHAPE), ("load", SHAPE)]
