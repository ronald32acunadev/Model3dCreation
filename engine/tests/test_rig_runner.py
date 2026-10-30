import json
import os
import sys
from pathlib import Path

import pytest

from model3d_contracts.jobs import JobOptions
from model3d_engine.stages.protocol import RIG_INPUT, StageError, StageTask
from model3d_engine.stages.rig import RigRunner, rig_availability

pytestmark = pytest.mark.anyio
STUB = Path(__file__).with_name("unirig_stub.py")
SKELETON = "skeleton/articulation-xl_quantization_256/model.ckpt"
SKIN = "skin/articulation-xl/model.ckpt"


def unirig_tree(root: Path, *, clone=True, venv=True, weights=True) -> tuple[Path, Path]:
    """A fake vendor/UniRig and data/hf with the pieces rig_availability looks for."""
    unirig, hf_home = root / "UniRig", root / "hf"
    if clone:
        unirig.mkdir(parents=True)
        (unirig / "run.py").write_text("")
    if clone and venv:
        (unirig / ".venv/bin").mkdir(parents=True)
        (unirig / ".venv/bin/python").symlink_to(sys.executable)
    if weights:
        snapshot = hf_home / "hub/models--VAST-AI--UniRig/snapshots/abc"
        for name in (SKELETON, SKIN):
            (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
            (snapshot / name).write_bytes(b"ckpt")
    return unirig, hf_home


def make_task(tmp_path: Path) -> StageTask:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / RIG_INPUT).write_bytes(b"glTF-model")
    return StageTask(job_id="j1", job_dir=job_dir, options=JobOptions(rig=True), seed=1)


def make_runner(tmp_path: Path, device="cuda:1", **kwargs) -> RigRunner:
    unirig, hf_home = unirig_tree(tmp_path)
    env = {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "HF_HOME": str(hf_home), "HF_HUB_OFFLINE": "1"}
    return RigRunner(unirig, hf_home, device, env=env, script=STUB, **kwargs)


async def test_rig_runner_forwards_progress_and_returns_result(tmp_path):
    runner = make_runner(tmp_path)
    task = make_task(tmp_path)
    seen = []
    result = await runner.run(task, lambda value, text: seen.append((value, text)))
    assert (0.4, "Skeleton ready") in seen
    assert (result.vertices, result.faces) == (8, 12)
    assert result.output == task.job_dir / "model.glb"
    assert result.output.read_bytes() == b"glTF-model"


async def test_rig_runner_maps_error_line_to_stage_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "oom")
    with pytest.raises(StageError) as raised:
        await make_runner(tmp_path).run(make_task(tmp_path), lambda *_: None)
    assert raised.value.code == "gpu_out_of_memory"
    assert raised.value.message == "CUDA out of memory"


async def test_rig_runner_crash_without_error_line_is_rig_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "crash")
    with pytest.raises(StageError) as raised:
        await make_runner(tmp_path).run(make_task(tmp_path), lambda *_: None)
    assert raised.value.code == "rig_failed"
    assert "boom happened" in raised.value.message


async def test_rig_runner_timeout_kills_process(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "sleep")
    runner = make_runner(tmp_path, task_timeout_s=0.5)
    with pytest.raises(StageError) as raised:
        await runner.run(make_task(tmp_path), lambda *_: None)
    assert raised.value.code == "stage_timeout"
    assert runner.last_pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(runner.last_pid, 0)


async def test_rig_runner_sets_cuda_and_hf_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "env")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    runner = make_runner(tmp_path, device="cuda:1")
    task = make_task(tmp_path)
    await runner.run(task, lambda *_: None)
    seen = json.loads(next(task.job_dir.rglob("env.json")).read_text())
    assert seen["CUDA_VISIBLE_DEVICES"] == "1"
    assert seen["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert seen["HF_HOME"] == str(tmp_path / "hf")
    assert seen["cwd"] == str(tmp_path / "UniRig")
    assert seen["args"]["max_faces"] == "150000"


async def test_rig_runner_maps_index_through_visible_devices(tmp_path, monkeypatch):
    """With the engine itself limited to some cards, device indices count within that list."""
    monkeypatch.setenv("STUB_MODE", "env")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-aaa,GPU-bbb")
    task = make_task(tmp_path)
    await make_runner(tmp_path, device="cuda:1").run(task, lambda *_: None)
    assert json.loads(next(task.job_dir.rglob("env.json")).read_text())["CUDA_VISIBLE_DEVICES"] == "GPU-bbb"


def test_rig_availability_reports_each_missing_piece(tmp_path):
    assert rig_availability(*unirig_tree(tmp_path / "ok")) == (True, None)
    assert rig_availability(*unirig_tree(tmp_path / "noclone", clone=False)) == (
        False,
        "UniRig is not installed in vendor/UniRig (see docs/setup.md)",
    )
    assert rig_availability(*unirig_tree(tmp_path / "novenv", venv=False)) == (
        False,
        "UniRig's environment is missing (see docs/setup.md)",
    )
    assert rig_availability(*unirig_tree(tmp_path / "noweights", weights=False)) == (
        False,
        "UniRig weights are missing from data/hf (see docs/setup.md)",
    )


async def test_runner_ready_follows_availability(tmp_path):
    unirig, hf_home = unirig_tree(tmp_path, weights=False)
    runner = RigRunner(unirig, hf_home, "cuda:0")
    await runner.start()
    assert runner.ready is False
    assert runner.reason == "UniRig weights are missing from data/hf (see docs/setup.md)"


async def test_cancelled_run_kills_unirig(tmp_path, monkeypatch):
    """The engine stopping mid-rig must not leave UniRig holding GPU memory."""
    import asyncio

    monkeypatch.setenv("STUB_MODE", "sleep")
    runner = make_runner(tmp_path)
    task = asyncio.create_task(runner.run(make_task(tmp_path), lambda *_: None))
    while runner.last_pid is None:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(runner.last_pid, 0)


async def test_malformed_progress_line_kills_unirig_and_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "badline")
    runner = make_runner(tmp_path)
    with pytest.raises(StageError) as raised:
        await runner.run(make_task(tmp_path), lambda *_: None)
    assert raised.value.code == "rig_failed"
    with pytest.raises(ProcessLookupError):
        os.kill(runner.last_pid, 0)
