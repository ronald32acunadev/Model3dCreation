"""TRELLIS.2 stage tests: the real worker protocol, driven by a fake worker (no GPU, no rembg)."""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from model3d_contracts.jobs import JobOptions, Quality
from model3d_engine.stages.protocol import StageError, StageTask
from model3d_engine.stages.trellis import TrellisStage

FAKE_WORKER = Path(__file__).with_name("trellis_fake_worker.py")


@pytest.fixture
def build_stage(tmp_path, monkeypatch):
    """Build a stage against the fake worker; every stage started here is closed afterwards."""
    (tmp_path / "trellis").mkdir(exist_ok=True)
    stages = []

    def build(mode: str = "ok", post_device: str | None = None) -> TrellisStage:
        monkeypatch.setenv("FAKE_TRELLIS_MODE", mode)
        stage = TrellisStage(
            str(tmp_path / "trellis"),
            "cuda:1",
            str(tmp_path / "hf"),
            python=sys.executable,
            worker_script=str(FAKE_WORKER),
            post_device=post_device,
        )
        stages.append(stage)
        return stage

    yield build
    for stage in stages:
        stage.close()


@pytest.fixture
def job_dir(tmp_path):
    folder = tmp_path / "job"
    folder.mkdir()
    image = Image.new("RGBA", (4, 4), (200, 30, 30, 255))
    image.putpixel((0, 0), (0, 0, 0, 0))
    image.save(folder / "input.png")
    return folder


def run_task(job_dir: Path, options: JobOptions, seed: int = 7) -> StageTask:
    return StageTask(job_id="job", job_dir=job_dir, options=options, seed=seed)


def test_missing_venv_names_the_folder(tmp_path):
    (tmp_path / "trellis").mkdir()
    with pytest.raises(StageError) as raised:
        TrellisStage(str(tmp_path / "trellis"), "cuda:0", str(tmp_path / "hf"))
    assert raised.value.code == "generator_unavailable"
    assert str(tmp_path / "trellis") in raised.value.message


def test_run_writes_textured_model_and_relays_progress(build_stage, job_dir):
    stage = build_stage()
    calls = []
    result = stage.run(run_task(job_dir, JobOptions(quality=Quality.high)), lambda v, m: calls.append((v, m)))

    assert result.output == job_dir / "model.glb"
    assert result.output.exists()
    assert result.vertices == 8
    assert result.faces == 12
    assert result.textured is True
    assert calls == [(0.3, "Generating the shape")]
    request = json.loads((job_dir / "model.glb.request.json").read_text())
    assert request["pipeline_type"] == "1024_cascade"
    assert request["faces"] == 150_000
    assert request["texture"] is True
    assert request["seed"] == 7
    assert request["image"] == str(job_dir / "trellis_input.png")


def test_draft_uses_512_and_untextured_writes_shape(build_stage, job_dir):
    stage = build_stage()
    result = stage.run(run_task(job_dir, JobOptions(quality=Quality.draft, texture=False)), lambda v, m: None)

    assert result.output == job_dir / "shape.glb"
    assert result.textured is False
    request = json.loads((job_dir / "shape.glb.request.json").read_text())
    assert request["pipeline_type"] == "512"
    assert request["faces"] == 40_000
    assert request["texture"] is False


def test_worker_start_error_is_reported(build_stage):
    with pytest.raises(StageError) as raised:
        build_stage("fail_load")
    assert raised.value.code == "worker_start_failed"
    assert raised.value.message == "model file busy"


def test_worker_run_error_is_reported(build_stage, job_dir):
    stage = build_stage("fail_run")
    with pytest.raises(StageError) as raised:
        stage.run(run_task(job_dir, JobOptions()), lambda v, m: None)
    assert raised.value.code == "gpu_out_of_memory"


def test_worker_crash_is_reported(build_stage, job_dir):
    stage = build_stage("crash_run")
    with pytest.raises(StageError) as raised:
        stage.run(run_task(job_dir, JobOptions()), lambda v, m: None)
    assert raised.value.code == "worker_crashed"
    assert "exited with code 3" in raised.value.message


def test_device_index_reaches_worker(build_stage, job_dir):
    stage = build_stage()
    stage.run(run_task(job_dir, JobOptions(quality=Quality.high)), lambda v, m: None)
    request = json.loads((job_dir / "model.glb.request.json").read_text())
    assert request["cuda_visible_devices"] == "1"
    assert request["cuda_device_order"] == "PCI_BUS_ID"


def test_post_device_reaches_worker(build_stage, job_dir):
    stage = build_stage(post_device="cuda:0")
    stage.run(run_task(job_dir, JobOptions(quality=Quality.high)), lambda v, m: None)
    request = json.loads((job_dir / "model.glb.request.json").read_text())
    assert request["cuda_visible_devices"] == "1,0"
    assert request["post_device"] == "cuda:1"


def test_same_post_device_is_one_card(build_stage, job_dir):
    stage = build_stage(post_device="cuda:1")
    stage.run(run_task(job_dir, JobOptions(quality=Quality.high)), lambda v, m: None)
    request = json.loads((job_dir / "model.glb.request.json").read_text())
    assert request["cuda_visible_devices"] == "1"
    assert request["post_device"] is None
