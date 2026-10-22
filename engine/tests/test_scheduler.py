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


async def test_swap_double_load_failure_reports_and_fails_jobs(tmp_path):
    """Final review #1: when the stage and the recovery both fail to load, the engine says why
    instead of staying not-ready with no error, and no queued job is left hanging."""
    scheduler, store, bus, log = await make_swap(tmp_path, shape_delay=0.2)
    shape, paint = scheduler.runners
    textured, _ = await submit(scheduler, store, bus)
    waiting, _ = await submit(scheduler, store, bus)
    while textured.state != JobState.shaping:
        await asyncio.sleep(0.01)
    paint.start_error = StageError("worker_start_failed", "paint out of memory while loading")
    shape.start_error = StageError("worker_start_failed", "shape out of memory while loading")
    await wait_final(textured)
    await wait_final(waiting)
    await scheduler.stop()
    assert textured.error.code == "worker_unavailable"
    assert waiting.state == JobState.failed
    assert scheduler.ready is False
    assert scheduler.start_error.code == "worker_unavailable"
    assert scheduler.start_error.message == "Neither the shape nor the paint worker could be loaded; see the engine log"


async def test_swap_unexpected_load_error_does_not_stop_the_loop(tmp_path):
    """Final review #5: an OSError from spawning a worker counts as a failed load."""
    scheduler, store, bus, log = await make_swap(tmp_path)
    shape, paint = scheduler.runners
    paint.start_error = OSError("too many open files")
    textured, _ = await submit(scheduler, store, bus)
    await wait_final(textured)
    untextured, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(untextured)
    await scheduler.stop()
    assert textured.error.code == "worker_unavailable"
    assert untextured.state == JobState.succeeded


async def test_swap_cancel_of_a_job_already_waiting_for_paint(tmp_path):
    """Plan Review Focus 4: a job in the paint queue cancelled while another shapes never loads paint."""
    scheduler, store, bus, log = await make_swap(tmp_path, shape_delay=0.2)
    first, _ = await submit(scheduler, store, bus)
    second, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    while second.state != JobState.shaping:
        await asyncio.sleep(0.01)
    assert first.state == JobState.shaping  # shaped, now queued for paint
    assert scheduler.cancel(first.job_id) is True
    await wait_final(first)
    await wait_final(second)
    await asyncio.sleep(0.1)
    await scheduler.stop()
    assert first.state == JobState.cancelled
    assert second.state == JobState.succeeded
    assert ("load", PAINT) not in log


RIG = "rigged.glb"
DUAL_CARDS = {"shape": "cuda:0", "paint": "cuda:1", "rig": "cuda:1"}


async def make_rig(tmp_path, swap=False, cards=DUAL_CARDS, rig_fits=True, delay=0.0, rig_error=None):
    log: list[tuple[str, str]] = []
    shape = FakeRunner(SHAPE, delay=delay, log=log)
    paint = FakeRunner(PAINT, delay=delay, log=log)
    rig = FakeRunner(RIG, delay=delay, log=log, fits_result=rig_fits, fail_with=rig_error)
    store, bus = JobStore(tmp_path), EventBus()
    scheduler = Scheduler(store, bus, shape, paint, swap=swap, rig=rig, cards=cards)
    await scheduler.start()
    return scheduler, store, bus, shape, paint, rig, log


def states(events) -> list[str]:
    return [getattr(event, "stage", None) or event.type for event in events]


async def test_untextured_rig_job_runs_shape_then_rig(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path)
    record, events = await submit(scheduler, store, bus, JobOptions(texture=False, rig=True))
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert [r[0] for r in rig.runs] == [record.job_id]
    assert paint.runs == []
    seen = await drain(events)
    assert "rig" in [getattr(event, "stage", None) for event in seen]
    assert seen[-1].stats.textured is False
    await scheduler.stop()


async def test_textured_rig_job_runs_all_three_stages_in_order(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path)
    record, events = await submit(scheduler, store, bus, JobOptions(rig=True))
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert shape.runs[0][2] <= paint.runs[0][1] and paint.runs[0][2] <= rig.runs[0][1]
    stages = [event.stage for event in await drain(events) if event.type == "progress"]
    assert stages.index("shape") < stages.index("paint") < stages.index("rig")
    await scheduler.stop()


async def test_rig_only_job_skips_shape_and_paint(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path)
    record = store.create_rig(b"glTF", textured=True)
    events = bus.subscribe(record)
    await events.__anext__()
    scheduler.submit_rig(record)
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert shape.runs == [] and paint.runs == []
    assert (await drain(events))[-1].stats.textured is True
    await scheduler.stop()


async def test_rig_never_overlaps_paint_on_shared_card(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path, delay=0.05)
    records = [(await submit(scheduler, store, bus, JobOptions(rig=True)))[0] for _ in range(3)]
    for record in records:
        await wait_final(record)
    for _job, p_start, p_end in paint.runs:
        for _job2, r_start, r_end in rig.runs:
            assert p_end <= r_start or r_end <= p_start
    await scheduler.stop()


async def test_rig_parks_paint_when_card_is_full_and_ready_stays_true(tmp_path):
    scheduler, store, bus, shape, paint, rig, log = await make_rig(tmp_path, rig_fits=False)
    ready_during_rig = []
    original = rig.run

    async def run(task, on_progress):
        ready_during_rig.append(scheduler.ready)
        return await original(task, on_progress)

    rig.run = run
    first, _ = await submit(scheduler, store, bus, JobOptions(rig=True))
    await wait_final(first)
    assert ("unload", PAINT) in log
    assert ready_during_rig == [True]
    assert scheduler.ready is True
    second, _ = await submit(scheduler, store, bus, JobOptions())
    await wait_final(second)
    assert second.state == JobState.succeeded
    assert log[-1] == ("load", PAINT)
    assert ("unload", SHAPE) not in log  # shape sits on the other card
    await scheduler.stop()


async def test_swap_mode_rig_unloads_loaded_worker_when_it_does_not_fit(tmp_path):
    one_card = {"shape": "cuda:0", "paint": "cuda:0", "rig": "cuda:0"}
    scheduler, store, bus, shape, paint, rig, log = await make_rig(tmp_path, swap=True, cards=one_card, rig_fits=False)
    record, _ = await submit(scheduler, store, bus, JobOptions(texture=False, rig=True))
    await wait_final(record)
    assert record.state == JobState.succeeded
    workers = [entry for entry in log if entry[1] != RIG]  # the rig fake logs its no-op start
    assert workers == [("load", SHAPE), ("unload", SHAPE)]
    following, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(following)
    assert following.state == JobState.succeeded
    assert log[-1] == ("load", SHAPE)
    await scheduler.stop()


async def test_rig_failure_fails_job_with_its_code(tmp_path):
    error = StageError("rig_failed", "UniRig skeleton step failed")
    scheduler, store, bus, *_rest = await make_rig(tmp_path, rig_error=error)
    record, events = await submit(scheduler, store, bus, JobOptions(texture=False, rig=True))
    await wait_final(record)
    assert record.state == JobState.failed
    assert (await drain(events))[-1].error.code == "rig_failed"
    await scheduler.stop()


async def test_cancel_while_painting_skips_rig(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path, delay=0.1)
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True))
    while record.state != JobState.painting:
        await asyncio.sleep(0.01)
    scheduler.cancel(record.job_id)
    await wait_final(record)
    assert record.state == JobState.cancelled
    assert rig.runs == []
    await scheduler.stop()


async def test_jobs_without_rig_never_touch_rig_runner(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path, rig_fits=False)
    for options in (JobOptions(), JobOptions(texture=False)):
        record, _ = await submit(scheduler, store, bus, options)
        await wait_final(record)
        assert record.state == JobState.succeeded
    assert rig.runs == []
    assert scheduler.rig_available is True
    await scheduler.stop()


async def test_cancel_queued_rig_only_job(tmp_path):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path, delay=0.2)
    busy = store.create_rig(b"glTF", textured=False)
    scheduler.submit_rig(busy)
    queued = store.create_rig(b"glTF", textured=False)
    scheduler.submit_rig(queued)
    await asyncio.sleep(0.05)
    assert scheduler.cancel(queued.job_id) is True
    await wait_final(queued)
    assert queued.state == JobState.cancelled
    await wait_final(busy)
    assert [run[0] for run in rig.runs] == [busy.job_id]
    await scheduler.stop()
