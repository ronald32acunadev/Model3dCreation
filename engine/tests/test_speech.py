import json
import time
import wave
from datetime import UTC, datetime

import pytest
from starlette.websockets import WebSocketDisconnect

from model3d_contracts.events import parse_event
from model3d_contracts.jobs import Job, JobOptions
from model3d_engine.api.app import create_app
from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.protocol import StageError
from model3d_engine.voices import VoiceKind, VoiceStore

from .fakes import FakeRunner, FakeVoiceRunner
from .test_api import MODE, ReadyClient, assert_error, post_job, wait_state

WS = "ws://127.0.0.1:8765"
UNAVAILABLE = "Qwen3-TTS is not installed in vendor/Qwen3-TTS"


def make(tmp_path, voice="default", shape=None, cards=None, swap=False):
    """A client, the store, the scheduler, and a saved voice; `voice` None means no voice stage."""
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    store, bus = JobStore(settings.jobs_dir), EventBus()
    runner = FakeVoiceRunner() if voice == "default" else voice
    scheduler = Scheduler(
        store, bus, shape or FakeRunner("shape.glb"), FakeRunner("model.glb"), swap=swap, cards=cards, voice=runner
    )
    reason = None if runner is not None else UNAVAILABLE
    app = create_app(
        settings, scheduler, store, bus,
        health=lambda: {
            "ready": scheduler.ready, "mode": MODE, "gpus": [],
            "voice": scheduler.voice_available, "voice_reason": reason,
        },
        voice_reason=lambda: reason,
    )
    client = ReadyClient(app, base_url="http://127.0.0.1:8765")
    client.wait_ready = True
    saved = VoiceStore(settings.voices_dir).create("Ana", VoiceKind.recorded, "es", "Hola, soy Ana.", b"RIFFsample")
    return client, store, scheduler, saved


def speak(client, voice_id, text="Hola mundo. Adios.", language="es"):
    return client.post("/v1/speech-jobs", json={"voice_id": voice_id, "language": language, "text": text})


def read_until_close(ws):
    events = []
    while True:
        try:
            events.append(parse_event(ws.receive_text()))
        except WebSocketDisconnect:
            return events


def test_submit_answers_202_with_a_speech_job(tmp_path):
    client, store, _, voice = make(tmp_path)
    with client:
        response = speak(client, voice.voice_id)
        assert response.status_code == 202
        job = response.json()
        assert job["kind"] == "speech"
        assert job["links"]["result"] == f"/v1/speech-jobs/{job['job_id']}/result"
        assert job["links"]["events"] == f"/v1/jobs/{job['job_id']}/events"
        assert job["queue_position"] is None
        assert store.get(job["job_id"]).kind == "speech"


def test_job_succeeds_and_the_result_is_a_wav(tmp_path):
    client, _, _, voice = make(tmp_path)
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        result = client.get(f"/v1/speech-jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.headers["content-type"] == "audio/wav"
        path = tmp_path / "jobs" / job_id / "speech.wav"
        assert result.content == path.read_bytes()
        with wave.open(str(path)) as wav:
            assert (wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (1, 24000, 2400)
        assert client.get(f"/v1/jobs/{job_id}/result").content == result.content


def test_result_is_409_until_the_job_is_done_and_404_for_other_jobs(tmp_path):
    client, _, _, voice = make(tmp_path, voice=FakeVoiceRunner(delay=0.5))
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        assert_error(client.get(f"/v1/speech-jobs/{job_id}/result"), 409, "job_not_finished")
        assert_error(client.get("/v1/speech-jobs/nope/result"), 404, "job_not_found")
        model_id = post_job(client).json()["job_id"]
        assert_error(client.get(f"/v1/speech-jobs/{model_id}/result"), 404, "job_not_found")


def test_progress_events_carry_the_voice_stage(tmp_path):
    client, _, _, voice = make(tmp_path, voice=FakeVoiceRunner(delay=0.3))
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        with client.websocket_connect(f"{WS}/v1/jobs/{job_id}/events") as ws:
            events = read_until_close(ws)
    assert [e.type for e in events][0] == "snapshot"
    progress = [e for e in events if e.type == "progress"]
    assert {e.stage for e in progress} == {"voice"}
    assert (progress[-1].progress, progress[-1].message) == (1.0, "sentence 2 of 2")
    completed = events[-1]
    assert completed.type == "completed"
    assert completed.result_url == f"/v1/speech-jobs/{job_id}/result"
    assert completed.stats is None


def test_request_file_holds_the_voice_sample_and_transcript(tmp_path):
    client, _, _, voice = make(tmp_path)
    with client:
        job_id = speak(client, voice.voice_id, text="  Hello there.  ", language="en").json()["job_id"]
        wait_state(client, job_id, "succeeded")
    request = json.loads((tmp_path / "jobs" / job_id / "speech_request.json").read_text())
    assert request == {
        "type": "speak",
        "text": "Hello there.",
        "language": "en",
        "ref_audio": str((tmp_path / "voices" / voice.voice_id / "sample.wav").resolve()),
        "ref_text": "Hola, soy Ana.",
        "instruct": None,
    }


def test_unknown_voice_is_404(tmp_path):
    client, store, _, _ = make(tmp_path)
    with client:
        assert_error(speak(client, "nope"), 404, "voice_not_found")
        assert not (tmp_path / "jobs").exists()


@pytest.mark.parametrize("text", ["", "   \n\t ", "x" * 2001])
def test_invalid_text_is_400(tmp_path, text):
    client, _, _, voice = make(tmp_path)
    with client:
        assert_error(speak(client, voice.voice_id, text=text), 400, "invalid_text")
        assert not (tmp_path / "jobs").exists()


def test_text_of_exactly_2000_characters_is_accepted(tmp_path):
    client, _, _, voice = make(tmp_path)
    with client:
        assert speak(client, voice.voice_id, text="x" * 2000).status_code == 202


def test_invalid_text_wins_over_unknown_voice_and_unknown_voice_over_unavailable(tmp_path):
    client, _, _, voice = make(tmp_path, voice=None)
    with client:
        assert_error(speak(client, "nope", text=" "), 400, "invalid_text")
        assert_error(speak(client, "nope"), 404, "voice_not_found")


def test_without_a_voice_stage_the_job_is_refused_with_the_reason(tmp_path):
    client, _, _, voice = make(tmp_path, voice=None)
    with client:
        error = assert_error(speak(client, voice.voice_id), 400, "voice_unavailable")
        assert error["message"] == UNAVAILABLE
        assert not (tmp_path / "jobs").exists()


def test_unsupported_language_is_rejected(tmp_path):
    client, _, _, voice = make(tmp_path)
    with client:
        assert speak(client, voice.voice_id, language="fr").status_code == 422


def test_health_reports_voice_availability_and_reason(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        body = client.get("/v1/health").json()
        assert (body["voice"], body["voice_reason"]) == (True, None)
    client, _, _, _ = make(tmp_path / "other", voice=None)
    with client:
        body = client.get("/v1/health").json()
        assert (body["voice"], body["voice_reason"]) == (False, UNAVAILABLE)


def test_a_failing_voice_stage_fails_the_job_with_its_error(tmp_path):
    client, _, _, voice = make(tmp_path, voice=FakeVoiceRunner(fail_with=StageError("voice_failed", "boom")))
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        body = wait_state(client, job_id, "failed")
    assert body["error"] == {"code": "voice_failed", "message": "boom"}


def test_speech_unloads_a_ready_shape_runner_on_the_same_card(tmp_path):
    log: list[tuple[str, str]] = []
    shape = FakeRunner("shape.glb", log=log)
    shape.ready = True
    client, _, _, voice = make(
        tmp_path, voice=FakeVoiceRunner(log=log), shape=shape, cards={"shape": "cuda:0", "voice": "cuda:0"}
    )
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
    assert log[:2] == [("unload", "shape.glb"), ("load", "speech.wav")]


def test_a_3d_job_unloads_a_ready_voice_runner_on_the_same_card(tmp_path):
    log: list[tuple[str, str]] = []
    voice_runner = FakeVoiceRunner(log=log)
    voice_runner.ready = True
    client, _, _, _ = make(
        tmp_path, voice=voice_runner, shape=FakeRunner("shape.glb", log=log), cards={"shape": "cuda:0", "voice": "cuda:0"}
    )
    with client:
        job_id = post_job(client, options={"texture": False}).json()["job_id"]
        wait_state(client, job_id, "succeeded")
    assert log[:2] == [("unload", "speech.wav"), ("load", "shape.glb")]


def test_a_voice_runner_on_another_card_is_left_loaded(tmp_path):
    log: list[tuple[str, str]] = []
    shape = FakeRunner("shape.glb", log=log)
    shape.ready = True
    client, _, _, voice = make(
        tmp_path, voice=FakeVoiceRunner(log=log), shape=shape, cards={"shape": "cuda:0", "voice": "cuda:1"}
    )
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        assert shape.ready
    assert ("unload", "shape.glb") not in log


def test_the_voice_worker_unloads_when_no_speech_job_waits(tmp_path):
    log: list[tuple[str, str]] = []
    client, _, _, voice = make(tmp_path, voice=FakeVoiceRunner(log=log))
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        deadline = time.monotonic() + 5
        while ("unload", "speech.wav") not in log:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert log == [("load", "speech.wav"), ("unload", "speech.wav")]


def test_swap_mode_runs_speech_and_frees_the_shape_worker(tmp_path):
    log: list[tuple[str, str]] = []
    shape = FakeRunner("shape.glb", log=log)
    shape.ready = True
    client, _, _, voice = make(tmp_path, voice=FakeVoiceRunner(log=log), shape=shape, swap=True)
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
    assert log[:2] == [("unload", "shape.glb"), ("load", "speech.wav")]


def test_cancel_of_a_queued_speech_job(tmp_path):
    runner = FakeVoiceRunner(delay=0.5)
    client, _, _, voice = make(tmp_path, voice=runner)
    with client:
        first = speak(client, voice.voice_id).json()["job_id"]
        second = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, first, "speaking")
        assert client.delete(f"/v1/jobs/{second}").status_code == 202
        assert wait_state(client, second, "cancelled")["kind"] == "speech"
        wait_state(client, first, "succeeded")
    assert [job_id for job_id, _, _ in runner.runs] == [first]


def test_old_job_json_without_kind_loads_as_a_model_job(tmp_path):
    client, store, _, _ = make(tmp_path)
    record = store.create(JobOptions(), b"x", ".png")
    store.persist(record)
    path = record.dir / "job.json"
    data = json.loads(path.read_text())
    assert data["kind"] == "model"
    del data["kind"]
    job = Job.model_validate(data)
    assert job.kind == "model"
    assert job.created_at <= datetime.now(UTC)
