import asyncio

import pytest

from model3d_contracts.jobs import JobOptions
from model3d_engine.stages.process import ProcessStageRunner
from model3d_engine.stages.protocol import StageError, StageTask

pytestmark = pytest.mark.anyio


def task(tmp_path) -> StageTask:
    return StageTask(job_id="job", job_dir=tmp_path, options=JobOptions(), seed=1)


def recorder():
    calls = []
    return calls, lambda value, message: calls.append((value, message))


async def test_runner_round_trip(tmp_path):
    runner = ProcessStageRunner("tests.worker_stubs:EchoStage", {}, {}, name="echo")
    await runner.start()
    try:
        assert runner.ready is True
        calls, on_progress = recorder()
        result = await runner.run(task(tmp_path), on_progress)
        assert result.output == tmp_path / "shape.glb" and result.output.exists()
        assert result.faces == 12
        assert [value for value, _ in calls] == [0.5]
    finally:
        await runner.stop()
    assert runner.ready is False


async def test_worker_env_is_applied(tmp_path):
    runner = ProcessStageRunner("tests.worker_stubs:EchoStage", {}, {"MODEL3D_TEST_FLAG": "on"}, name="echo")
    await runner.start()
    try:
        calls, on_progress = recorder()
        await runner.run(task(tmp_path), on_progress)
        assert calls == [(0.5, "on")]
    finally:
        await runner.stop()


async def test_stage_error_is_forwarded(tmp_path):
    runner = ProcessStageRunner("tests.worker_stubs:EchoStage", {"mode": "oom"}, {}, name="paint")
    await runner.start()
    try:
        with pytest.raises(StageError) as raised:
            await runner.run(task(tmp_path), lambda v, m: None)
        assert raised.value.code == "gpu_out_of_memory"
        assert runner.ready is True
    finally:
        await runner.stop()


async def test_crash_fails_task_and_restarts(tmp_path):
    runner = ProcessStageRunner("tests.worker_stubs:EchoStage", {"mode": "crash"}, {}, name="paint")
    await runner.start()
    try:
        with pytest.raises(StageError) as raised:
            await runner.run(task(tmp_path), lambda v, m: None)
        assert raised.value.code == "worker_crashed"
        assert "paint worker exited with code 3" in raised.value.message

        async def until_ready():
            while not runner.ready:
                await asyncio.sleep(0.05)

        await asyncio.wait_for(until_ready(), 30)
    finally:
        await runner.stop()


async def test_factory_failure_raises_on_start():
    runner = ProcessStageRunner("tests.worker_stubs:BrokenStage", {}, {}, name="shape")
    with pytest.raises(StageError) as raised:
        await runner.start()
    assert raised.value.code == "worker_start_failed"
    assert "no model here" in raised.value.message
    assert runner.ready is False
    await runner.stop()


async def test_next_job_waits_for_restart_after_crash(tmp_path):
    """Review finding 1: the job queued behind a crash must run once the worker is back."""
    from model3d_engine.jobs.events import EventBus
    from model3d_engine.jobs.scheduler import Scheduler
    from model3d_engine.jobs.store import JobStore

    marker = str(tmp_path / "shape")
    shape = ProcessStageRunner("tests.worker_stubs:EchoStage", {"mode": "crash_once", "marker": marker}, {}, name="shape")
    paint = ProcessStageRunner("tests.worker_stubs:EchoStage", {}, {}, name="paint")
    store = JobStore(tmp_path / "jobs")
    scheduler = Scheduler(store, EventBus(), shape, paint)
    await scheduler.start()
    try:
        first = store.create(JobOptions(texture=False), b"png", ".png")
        second = store.create(JobOptions(texture=False), b"png", ".png")
        scheduler.submit(first)
        scheduler.submit(second)

        async def until_final():
            while not (first.is_final and second.is_final):
                await asyncio.sleep(0.05)

        await asyncio.wait_for(until_final(), 60)
    finally:
        await scheduler.stop()
    assert first.error.code == "worker_crashed"
    assert second.state.value == "succeeded", second.error


async def test_failed_reload_is_logged_and_retried(tmp_path, caplog):
    """Review finding 3: a worker that fails to reload is retried, and the failure is logged."""
    marker = str(tmp_path / "paint")
    runner = ProcessStageRunner(
        "tests.worker_stubs:EchoStage",
        {"mode": "crash_then_fail_load", "marker": marker},
        {},
        name="paint",
        restart_delays=(0.0, 0.1),
    )
    await runner.start()
    try:
        with pytest.raises(StageError):
            await runner.run(task(tmp_path), lambda v, m: None)
        result = await runner.run(task(tmp_path), lambda v, m: None)
        assert result.output.exists()
    finally:
        await runner.stop()
    assert "paint worker failed to restart" in caplog.text
    assert "model file busy" in caplog.text


async def test_worker_killed_while_idle_is_restarted(tmp_path):
    """Review finding R3-idle-crash-no-restart: a worker that dies between jobs is restarted
    before the next job, instead of failing every later job on a broken pipe."""
    runner = ProcessStageRunner("tests.worker_stubs:EchoStage", {}, {}, name="shape")
    await runner.start()
    try:
        runner._process.kill()
        await asyncio.get_running_loop().run_in_executor(None, runner._process.join, 5)
        result = await runner.run(task(tmp_path), lambda v, m: None)
        assert result.output.exists()
        assert runner.ready is True
    finally:
        await runner.stop()


async def test_stuck_task_times_out_and_worker_restarts(tmp_path):
    """Review advisory: a worker alive but stuck in native code fails its task with stage_timeout
    and is restarted, so the stage is not blocked forever."""
    marker = str(tmp_path / "shape")
    runner = ProcessStageRunner(
        "tests.worker_stubs:EchoStage", {"mode": "hang_once", "marker": marker}, {}, name="shape", task_timeout_s=1.0
    )
    await runner.start()
    try:
        stuck = runner._process
        with pytest.raises(StageError) as raised:
            await asyncio.wait_for(runner.run(task(tmp_path), lambda v, m: None), 20)
        assert raised.value.code == "stage_timeout"
        assert "shape did not finish within 1 s" in raised.value.message
        assert not stuck.is_alive()
        result = await asyncio.wait_for(runner.run(task(tmp_path), lambda v, m: None), 30)
        assert result.output.exists()
        assert runner.ready is True
    finally:
        await runner.stop()
