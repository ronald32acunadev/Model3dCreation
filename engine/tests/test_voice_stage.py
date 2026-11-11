"""Voice stage tests: the real worker protocol, driven by a fake worker (no GPU, no Qwen3-TTS)."""

import json
import sys
import wave
from pathlib import Path

import pytest

from model3d_contracts.jobs import JobOptions
from model3d_engine.stages.protocol import StageError, StageTask
from model3d_engine.stages.voice import VoiceStage

FAKE_WORKER = Path(__file__).with_name("voice_fake_worker.py")
REQUEST = {
    "type": "speak",
    "text": "Hola mundo. Adios!",
    "language": "es",
    "ref_audio": "/voices/ref.wav",
    "ref_text": "Hola",
    "instruct": None,
}


@pytest.fixture
def build_stage(tmp_path, monkeypatch):
    """Build a stage against the fake worker; every stage started here is closed afterwards."""
    stages = []

    def build(mode: str = "ok") -> VoiceStage:
        monkeypatch.setenv("FAKE_VOICE_MODE", mode)
        stage = VoiceStage(
            tmp_path,
            "cuda:1",
            tmp_path / "hf",
            python=sys.executable,
            worker_script=str(FAKE_WORKER),
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
    (folder / "speech_request.json").write_text(json.dumps(REQUEST))
    return folder


def run_task(job_dir: Path) -> StageTask:
    return StageTask(job_id="job", job_dir=job_dir, options=JobOptions(), seed=7)


def test_run_writes_speech_wav(build_stage, job_dir):
    stage = build_stage()
    result = stage.run(run_task(job_dir), lambda v, m: None)

    assert result.output == job_dir / "speech.wav"
    assert result.vertices == 0
    assert result.faces == 0
    with wave.open(str(result.output)) as handle:
        assert handle.getframerate() == 24000
        assert handle.getnchannels() == 1


def test_progress_is_relayed_in_order(build_stage, job_dir):
    stage = build_stage()
    calls = []
    stage.run(run_task(job_dir), lambda v, m: calls.append((v, m)))

    assert calls == [(0.5, "Sentence 1 of 2"), (1.0, "Sentence 2 of 2")]


def test_request_reaches_worker_with_device_and_offline_env(build_stage, job_dir):
    stage = build_stage()
    stage.run(run_task(job_dir), lambda v, m: None)

    request = json.loads((job_dir / "speech.wav.request.json").read_text())
    assert request["type"] == "run"
    assert request["mode"] == "speak"
    assert request["text"] == "Hola mundo. Adios!"
    assert request["language"] == "es"
    assert request["ref_audio"] == "/voices/ref.wav"
    assert request["ref_text"] == "Hola"
    assert request["output"] == str(job_dir / "speech.wav")
    assert request["cuda_visible_devices"] == "1"
    assert request["hf_home_offline"] == "1"


def test_missing_request_file_is_invalid_request(build_stage, job_dir):
    (job_dir / "speech_request.json").unlink()
    stage = build_stage()
    with pytest.raises(StageError) as raised:
        stage.run(run_task(job_dir), lambda v, m: None)
    assert raised.value.code == "invalid_request"


def test_worker_start_error_is_reported(build_stage):
    with pytest.raises(StageError) as raised:
        build_stage("fail_load")
    assert raised.value.code == "worker_start_failed"
    assert raised.value.message == "model file busy"


def test_worker_run_error_is_reported(build_stage, job_dir):
    stage = build_stage("fail_run")
    with pytest.raises(StageError) as raised:
        stage.run(run_task(job_dir), lambda v, m: None)
    assert raised.value.code == "speech_failed"
    assert raised.value.message == "Qwen3-TTS failed: test"


def test_worker_crash_is_reported(build_stage, job_dir):
    stage = build_stage("crash_run")
    with pytest.raises(StageError) as raised:
        stage.run(run_task(job_dir), lambda v, m: None)
    assert raised.value.code == "worker_crashed"
    assert "exited with code 3" in raised.value.message


def test_missing_python_is_voice_unavailable(tmp_path):
    python = tmp_path / "missing" / "python"
    with pytest.raises(StageError) as raised:
        VoiceStage(tmp_path, "cuda:1", tmp_path / "hf", python=str(python), worker_script=str(FAKE_WORKER))
    assert raised.value.code == "voice_unavailable"
    assert str(python) in raised.value.message


def test_cpu_device_is_voice_unavailable(tmp_path):
    with pytest.raises(StageError) as raised:
        VoiceStage(tmp_path, "cpu", tmp_path / "hf", python=sys.executable, worker_script=str(FAKE_WORKER))
    assert raised.value.code == "voice_unavailable"
    assert raised.value.message == "Qwen3-TTS needs a CUDA device, got cpu"
