import asyncio

import pytest

from model3d_contracts.jobs import JobOptions, JobState
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.protocol import ProgressCallback, StageError, StageResult, StageTask

from .fakes import FakeRunner

pytestmark = pytest.mark.anyio

SHAPE, PAINT = "shape.glb", "model.glb"


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


def log_runs(runner, log: list[tuple[str, str]]) -> None:
    """Record each run of `runner` in `log`, between the loads and unloads around it."""
    run = runner.run

    async def logged(task, on_progress):
        log.append(("run", runner.output_name))
        return await run(task, on_progress)

    runner.run = logged


async def make_logged(tmp_path, swap=False, cards=None):
    """Dual or swap scheduler whose runners share one log of loads, runs, and unloads."""
    log: list[tuple[str, str]] = []
    shape, paint = FakeRunner(SHAPE, log=log), FakeRunner(PAINT, log=log)
    log_runs(shape, log)
    log_runs(paint, log)
    store, bus = JobStore(tmp_path), EventBus()
    scheduler = Scheduler(store, bus, shape, paint, swap=swap, cards=cards)
    await scheduler.start()
    return scheduler, store, bus, shape, paint, log


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


@pytest.mark.parametrize("swap", [False, True], ids=["dual", "swap"])
async def test_start_loads_no_model(tmp_path, swap):
    scheduler, _, _, shape, paint, log = await make_logged(tmp_path, swap=swap)
    assert scheduler.ready is True
    assert log == []
    assert shape.ready is False and paint.ready is False
    await scheduler.stop()


async def test_textured_job_loads_and_releases_each_stage(tmp_path):
    scheduler, store, bus, shape, paint, log = await make_logged(tmp_path)
    record, _ = await submit(scheduler, store, bus)
    await wait_final(record)
    await scheduler.stop()

    assert record.state == JobState.succeeded
    assert log == [
        ("load", SHAPE), ("run", SHAPE), ("unload", SHAPE),
        ("load", PAINT), ("run", PAINT), ("unload", PAINT),
    ]
    assert shape.ready is False and paint.ready is False


@pytest.mark.parametrize("swap", [False, True], ids=["dual", "swap"])
async def test_jobs_in_a_row_share_one_load(tmp_path, swap):
    scheduler, store, _, _, _, log = await make_logged(tmp_path, swap=swap)
    first = store.create(JobOptions(texture=False), b"png", ".png")
    second = store.create(JobOptions(texture=False), b"png", ".png")
    scheduler.submit(first)
    scheduler.submit(second)
    await wait_final(first)
    await wait_final(second)
    await scheduler.stop()

    assert [first.state, second.state] == [JobState.succeeded] * 2
    assert log == [("load", SHAPE), ("run", SHAPE), ("run", SHAPE), ("unload", SHAPE)]


@pytest.mark.parametrize("swap", [False, True], ids=["dual", "swap"])
async def test_job_progress_says_the_model_is_loading(tmp_path, swap):
    scheduler, store, bus, _, _, _ = await make_logged(tmp_path, swap=swap)
    record, events = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(record)
    received = await drain(events)
    await scheduler.stop()

    messages = [(event.stage, event.message) for event in received if event.type == "progress"]
    assert messages.index(("shape", "Loading shape model")) < messages.index(("shape", "halfway"))


@pytest.mark.parametrize("swap", [False, True], ids=["dual", "swap"])
async def test_failed_load_fails_the_job_and_the_engine_goes_on(tmp_path, swap):
    scheduler, store, bus, shape, _, _ = await make_logged(tmp_path, swap=swap)
    shape.start_error = StageError("worker_start_failed", "no weights")
    failed, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(failed)
    assert (failed.state, failed.error.code, failed.error.message) == (
        JobState.failed,
        "worker_start_failed",
        "no weights",
    )
    assert scheduler.ready is True

    shape.start_error = None
    retried, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(retried)
    await scheduler.stop()
    assert retried.state == JobState.succeeded


async def test_cancel_while_loading_ends_cancelled_and_releases(tmp_path):
    scheduler, store, bus, shape, _, log = await make_logged(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()
    load = shape.start

    async def gated_start():
        entered.set()
        await gate.wait()
        await load()

    shape.start = gated_start
    record, events = await submit(scheduler, store, bus, JobOptions(texture=False))
    await asyncio.wait_for(entered.wait(), 5)
    assert scheduler.cancel(record.job_id) is True
    gate.set()
    await wait_final(record)
    received = await drain(events)
    await scheduler.stop()

    assert record.state == JobState.cancelled
    assert received[-1].type == "cancelled"
    assert ("run", SHAPE) not in log
    assert shape.ready is False


class FlakyUnload(FakeRunner):
    """Its first `unload_failures` unloads raise, as a busy driver would."""

    def __init__(self, *args, unload_failures: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.unload_failures = unload_failures

    async def unload(self) -> None:
        if self.unload_failures:
            self.unload_failures -= 1
            raise RuntimeError("driver busy")
        await super().unload()


async def test_failed_unload_does_not_stop_the_loop(tmp_path):
    shape = FlakyUnload(SHAPE, unload_failures=1)
    scheduler, store, bus, _, _ = await make(tmp_path, shape=shape)
    first, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(first)
    second, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(second)
    await scheduler.stop()

    assert [first.state, second.state] == [JobState.succeeded] * 2
    assert len(shape.runs) == 2


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
        if action == "run":
            continue
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
    assert log == [("load", SHAPE), ("unload", SHAPE), ("load", PAINT), ("unload", PAINT)]
    assert never_both_loaded(log)


async def test_swap_textured_job_loads_and_releases_each_stage(tmp_path):
    scheduler, store, bus, _, _, log = await make_logged(tmp_path, swap=True)
    record, _ = await submit(scheduler, store, bus)
    await wait_final(record)
    await scheduler.stop()

    assert record.state == JobState.succeeded
    assert log == [
        ("load", SHAPE), ("run", SHAPE), ("unload", SHAPE),
        ("load", PAINT), ("run", PAINT), ("unload", PAINT),
    ]
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
        ("load", SHAPE), ("unload", SHAPE), ("load", PAINT), ("unload", PAINT),
    ]
    assert never_both_loaded(log)


async def test_swap_untextured_jobs_never_load_paint(tmp_path):
    scheduler, store, bus, log = await make_swap(tmp_path)
    records = [(await submit(scheduler, store, bus, JobOptions(texture=False)))[0] for _ in range(2)]
    for record in records:
        await wait_final(record)
    await scheduler.stop()
    assert [record.state for record in records] == [JobState.succeeded] * 2
    assert log == [("load", SHAPE), ("unload", SHAPE)]


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


async def test_swap_paint_load_failure_fails_the_textured_job(tmp_path):
    error = StageError("worker_start_failed", "paint worker exited with code 1 while loading")
    scheduler, store, bus, log = await make_swap(tmp_path, paint_start_error=error)
    textured, _ = await submit(scheduler, store, bus)
    await wait_final(textured)
    untextured, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(untextured)
    await scheduler.stop()
    assert textured.state == JobState.failed
    assert (textured.error.code, textured.error.message) == (
        "worker_start_failed",
        "paint worker exited with code 1 while loading",
    )
    assert untextured.state == JobState.succeeded
    assert log == [("load", SHAPE), ("unload", SHAPE), ("load", SHAPE), ("unload", SHAPE)]


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


async def make_rig(tmp_path, swap=False, cards=DUAL_CARDS, delay=0.0, rig_error=None):
    log: list[tuple[str, str]] = []
    shape = FakeRunner(SHAPE, delay=delay, log=log)
    paint = FakeRunner(PAINT, delay=delay, log=log)
    rig = FakeRunner(RIG, delay=delay, log=log, fail_with=rig_error)
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


@pytest.mark.parametrize("swap", [False, True], ids=["dual", "swap"])
async def test_rig_runs_with_no_worker_on_its_card(tmp_path, swap):
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path, swap=swap)
    readiness = []
    run = rig.run

    async def rig_run(task, on_progress):
        readiness.append((scheduler.ready, shape.ready, paint.ready))
        return await run(task, on_progress)

    rig.run = rig_run
    rigged, _ = await submit(scheduler, store, bus, JobOptions(rig=True))
    waiting, _ = await submit(scheduler, store, bus, JobOptions())
    await wait_final(rigged)
    await wait_final(waiting)
    await scheduler.stop()

    assert [rigged.state, waiting.state] == [JobState.succeeded] * 2
    assert len(readiness) == 1
    engine_ready, shape_ready, paint_ready = readiness[0]
    assert engine_ready is True
    assert paint_ready is False  # paint shares the rig card in dual mode
    if swap:
        assert shape_ready is False


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
    scheduler, store, bus, shape, paint, rig, _log = await make_rig(tmp_path)
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


class UnloadFailsTwice(FakeRunner):
    """Its first two unloads raise, as a busy driver would."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unload_failures = 2

    async def unload(self) -> None:
        if self.unload_failures:
            self.unload_failures -= 1
            raise RuntimeError("driver busy")
        await super().unload()


async def test_failed_unload_fails_the_rig_job_and_keeps_the_rig_loop(tmp_path):
    log: list[tuple[str, str]] = []
    shape, paint = FakeRunner(SHAPE, log=log), UnloadFailsTwice(PAINT, log=log)
    rig = FakeRunner(RIG, log=log)
    store, bus = JobStore(tmp_path), EventBus()
    scheduler = Scheduler(store, bus, shape, paint, rig=rig, cards=DUAL_CARDS)
    await scheduler.start()
    first, events = await submit(scheduler, store, bus, JobOptions(rig=True))
    await wait_final(first)
    assert first.state == JobState.failed
    assert (await drain(events))[-1].error.code == "worker_unavailable"
    second, _ = await submit(scheduler, store, bus, JobOptions(texture=False, rig=True))
    await wait_final(second)
    assert second.state == JobState.succeeded
    await scheduler.stop()


class MissingOutputOnce(FakeRunner):
    """Its first result names a file that was never written, so the engine fails that job itself."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.broken = True

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult:
        if self.broken:
            self.broken = False
            return StageResult(output=task.job_dir / "missing.glb", vertices=1, faces=1)
        return await super().run(task, on_progress)


async def test_unexpected_error_in_a_job_does_not_end_its_stage_loop(tmp_path):
    scheduler, store, bus, _, _ = await make(tmp_path, shape=MissingOutputOnce("shape.glb"))
    first, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(first)
    assert first.state == JobState.failed
    assert first.error.code == "internal_error"
    second, _ = await submit(scheduler, store, bus, JobOptions(texture=False))
    await wait_final(second)
    await scheduler.stop()
    assert second.state == JobState.succeeded


class TexturedRunner(FakeRunner):
    """A shape stage that also textures, like TRELLIS.2: its result is final."""

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult:
        result = await super().run(task, on_progress)
        return StageResult(output=result.output, vertices=result.vertices, faces=result.faces, textured=True)


async def test_textured_shape_result_skips_paint(tmp_path):
    scheduler, store, bus, _, paint = await make(tmp_path, shape=TexturedRunner("model.glb"))
    record, events = await submit(scheduler, store, bus)
    await wait_final(record)
    received = await drain(events)
    await scheduler.stop()

    assert record.state == JobState.succeeded
    assert paint.runs == []
    assert (record.dir / "model.glb").exists()
    assert received[-1].stats.textured is True
