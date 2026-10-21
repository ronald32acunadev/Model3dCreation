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
