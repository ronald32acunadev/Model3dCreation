"""The Sharper texture step: after the skeleton, before Detailed face, holding the cards."""

import asyncio

import pytest

from model3d_contracts.jobs import JobOptions, JobState
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore

from .fakes import FakeHeadPass, FakeRunner
from .test_scheduler import DUAL_CARDS, box_bytes, drain, submit, wait_final

pytestmark = pytest.mark.anyio


async def make_texture(tmp_path, mode="applied", face_mode=None, during=None, swap=False):
    store, bus = JobStore(tmp_path), EventBus()
    shape, paint, rig = FakeRunner("shape.glb"), FakeRunner("model.glb"), FakeRunner("model.glb")
    (tmp_path / "texture").mkdir(exist_ok=True)
    texture = FakeHeadPass(tmp_path / "texture", mode=mode, during=during)
    head = FakeHeadPass(tmp_path, mode=face_mode) if face_mode is not None else None
    scheduler = Scheduler(store, bus, shape, paint, swap=swap, rig=rig, cards=DUAL_CARDS, head_pass=head,
                          texture_pass=texture)
    await scheduler.start()
    return scheduler, store, bus, texture, head


@pytest.mark.parametrize("swap", [False, True], ids=["dual", "swap"])
async def test_sharper_texture_replaces_the_model_and_keeps_the_original(tmp_path, swap):
    seen: list[JobState] = []

    async def during(job_id):
        seen.append(store.get(job_id).state)

    scheduler, store, bus, texture, _ = await make_texture(tmp_path, during=during, swap=swap)
    record, events = await submit(scheduler, store, bus, JobOptions(rig=True, sharper_texture=True))
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert seen == [JobState.texturing]
    assert record.notice is None
    assert (record.dir / "model.glb").read_bytes() == texture.model.read_bytes()
    assert (record.dir / "model_original.glb").read_bytes() == box_bytes(tmp_path)
    evs = await drain(events)
    assert any(e.type == "progress" and e.stage == "texture" and e.message == "step one" for e in evs)
    await scheduler.stop()


async def test_texture_runs_before_detailed_face_and_the_original_stays_the_first_model(tmp_path):
    order: list[str] = []

    async def during(job_id):
        order.append(store.get(job_id).state.value)

    scheduler, store, bus, texture, head = await make_texture(tmp_path, during=during, face_mode="applied")
    head.during = during
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True, sharper_texture=True, detailed_face=True))
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert order == ["texturing", "detailing"]
    assert (record.dir / "model.glb").read_bytes() == head.model.read_bytes()
    assert (record.dir / "model_original.glb").read_bytes() == box_bytes(tmp_path)
    await scheduler.stop()


@pytest.mark.parametrize(
    ("mode", "notice"),
    [("skipped", "no usable head"), ("fail", "Sharper texture failed: boom: bad thing")],
)
async def test_a_texture_pass_that_is_not_applied_keeps_the_model(tmp_path, mode, notice):
    scheduler, store, bus, _, _ = await make_texture(tmp_path, mode=mode)
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True, sharper_texture=True))
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert record.notice == notice
    assert (record.dir / "model.glb").read_bytes() == box_bytes(tmp_path)
    assert not (record.dir / "model_original.glb").exists()
    await scheduler.stop()


async def test_both_notices_are_kept_when_both_passes_are_skipped(tmp_path):
    scheduler, store, bus, _, head = await make_texture(tmp_path, mode="fail", face_mode="skipped")
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True, sharper_texture=True, detailed_face=True))
    await wait_final(record)
    assert record.notice == "Sharper texture failed: boom: bad thing; no usable head"
    await scheduler.stop()


async def test_cancel_while_texturing_stops_the_pass_and_keeps_the_model(tmp_path):
    scheduler, store, bus, texture, _ = await make_texture(tmp_path, mode="wait")
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True, sharper_texture=True))
    await asyncio.wait_for(texture.started.wait(), 5)
    assert scheduler.cancel(record.job_id) is True
    await wait_final(record)
    assert record.state == JobState.succeeded
    assert record.notice == "Sharper texture was cancelled"
    assert texture.cancelled.is_set()
    assert (record.dir / "model.glb").read_bytes() == box_bytes(tmp_path)
    await scheduler.stop()


async def test_other_jobs_wait_for_the_cards_while_the_texture_pass_runs(tmp_path):
    ctx: dict = {}

    async def during(job_id):
        sub = ctx["store"].create(JobOptions(), b"png", ".png")
        ctx["scheduler"].submit(sub)
        await asyncio.sleep(0.3)
        shape = ctx["scheduler"]._shape
        ctx["shape_during"] = (shape.ready, [run[0] for run in shape.runs].count(sub.job_id))
        ctx["sub"] = sub

    scheduler, store, bus, _, _ = await make_texture(tmp_path, during=during)
    ctx["scheduler"], ctx["store"] = scheduler, store
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True, sharper_texture=True))
    await wait_final(record, 10)
    await wait_final(ctx["sub"], 10)
    assert ctx["shape_during"] == (False, 0)  # the GPUs belong to the texture pass: no model loaded or run
    assert ctx["sub"].state == JobState.succeeded
    await scheduler.stop()


async def test_without_sharper_texture_the_texture_pass_is_not_used(tmp_path):
    scheduler, store, bus, texture, _ = await make_texture(tmp_path)
    record, _ = await submit(scheduler, store, bus, JobOptions(rig=True))
    await wait_final(record)
    assert texture.calls == []
    await scheduler.stop()
