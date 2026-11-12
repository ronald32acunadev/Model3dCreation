import pytest

from fake_engine import CLOSE, FakeEngine, error_body, make_job
from fakes import FakePlayer, FakeRecorder, FakeSupervisor
from model3d_client.services.supervisor import EngineState
from model3d_client.viewmodels.voice import VoiceViewModel

TS = "2026-11-01T12:00:00Z"


def voice_json(voice_id="V1", name="Narrator", kind="designed", language="es") -> dict:
    return {"voice_id": voice_id, "name": name, "kind": kind, "language": language,
            "created_at": TS, "transcript": "Hola mundo"}


def progress(job_id, value=0.5, message="Speaking"):
    return {"type": "progress", "job_id": job_id, "ts": TS, "stage": "voice", "progress": value, "message": message}


def completed(job_id):
    return {"type": "completed", "job_id": job_id, "ts": TS, "result_url": f"/v1/speech-jobs/{job_id}/result"}


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def recorder():
    return FakeRecorder()


@pytest.fixture
def make_vm(make_api, engine, player, recorder, tmp_path):
    def make(supervisor=None):
        supervisor = supervisor or FakeSupervisor(voice=True)
        return VoiceViewModel(make_api(engine.url), supervisor, tmp_path / "cache", player, lambda: recorder)

    return make


def requests(engine, method, path):
    return [r for r in engine.controller.voice_requests if (r["method"], r["path"]) == (method, path)]


def script_voices(engine, *voices):
    engine.controller.voice_answers["GET /v1/voices"] = (200, list(voices))


def test_refresh_lists_voices(qtbot, engine, make_vm):
    script_voices(engine, voice_json("V1"), voice_json("V2", "Hero", "recorded", "en"))
    vm = make_vm()
    vm.refresh()
    qtbot.waitUntil(lambda: len(vm.voices) == 2, timeout=5000)
    assert [v.name for v in vm.voices] == ["Narrator", "Hero"]


def test_rename_patches_then_refreshes(qtbot, engine, make_vm):
    script_voices(engine, voice_json("V1", "Renamed"))
    engine.controller.voice_answers["PATCH /v1/voices/V1"] = (200, voice_json("V1", "Renamed"))
    vm = make_vm()
    vm.rename("V1", "Renamed")
    qtbot.waitUntil(lambda: [v.name for v in vm.voices] == ["Renamed"], timeout=5000)
    assert requests(engine, "PATCH", "/v1/voices/V1")[0]["json"] == {"name": "Renamed"}


def test_delete_removes_then_refreshes(qtbot, engine, make_vm):
    script_voices(engine)
    engine.controller.voice_answers["DELETE /v1/voices/V1"] = (204, None)
    vm = make_vm()
    vm.delete("V1")
    qtbot.waitUntil(lambda: bool(requests(engine, "GET", "/v1/voices")), timeout=5000)
    assert requests(engine, "DELETE", "/v1/voices/V1")


def test_play_sample_downloads_to_cache_and_plays(qtbot, engine, make_vm, player):
    engine.controller.voice_files["GET /v1/voices/V1/sample"] = b"RIFF sample"
    vm = make_vm()
    vm.play_sample("V1")
    qtbot.waitUntil(lambda: bool(player.played), timeout=5000)
    assert player.played[0].read_bytes() == b"RIFF sample"


def test_preview_follows_job_then_plays_and_saves(qtbot, engine, make_vm, player):
    engine.controller.voice_answers["POST /v1/voice-previews"] = (202, make_job("PV1"))
    engine.controller.voice_files["GET /v1/speech-jobs/PV1/result"] = b"RIFF preview"
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json("V9", "Deep"))
    script_voices(engine, voice_json("V9", "Deep"))
    engine.controller.push(progress("PV1"), completed("PV1"), CLOSE)
    vm = make_vm()
    vm.preview("a deep voice", "en", "Hello")
    assert vm.previewing
    qtbot.waitUntil(lambda: vm.preview_ready, timeout=10000)
    assert vm.preview_id == "PV1" and not vm.previewing
    assert requests(engine, "POST", "/v1/voice-previews")[0]["json"] == {
        "description": "a deep voice", "language": "en", "sample_text": "Hello"}
    assert player.played == [vm.preview_path] and vm.preview_path.read_bytes() == b"RIFF preview"
    vm.save_preview("Deep")
    qtbot.waitUntil(lambda: not vm.preview_ready and [v.voice_id for v in vm.voices] == ["V9"], timeout=5000)
    assert requests(engine, "POST", "/v1/voices")[0]["json"] == {"name": "Deep", "kind": "designed", "preview_id": "PV1"}


def test_preview_failure_surfaces_the_job_error(qtbot, engine, make_vm):
    engine.controller.voice_answers["POST /v1/voice-previews"] = (202, make_job("PV2"))
    engine.controller.push({"type": "failed", "job_id": "PV2", "ts": TS,
                            "error": {"code": "gpu_out_of_memory", "message": "oom"}}, CLOSE)
    vm = make_vm()
    vm.preview("d", "en", "t")
    qtbot.waitUntil(lambda: vm.error is not None, timeout=10000)
    assert vm.error.code == "gpu_out_of_memory" and not vm.previewing and not vm.preview_ready


def test_recording_then_save_exposes_the_transcript(qtbot, engine, make_vm, recorder, player):
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json("V5", "Mine", "recorded", "es"))
    script_voices(engine, voice_json("V5", "Mine", "recorded", "es"))
    vm = make_vm()
    vm.start_recording()
    assert vm.recording and player.stops == 1
    recorder.level.emit(0.4)
    recorder.elapsed.emit(2.5)
    assert (vm.level, vm.elapsed) == (0.4, 2.5)
    vm.stop_recording()
    assert not vm.recording and vm.take == recorder.target
    vm.play_take()
    assert player.played == [vm.take]
    take = vm.take
    vm.save_recording("Mine", "es")
    assert vm.transcribing
    qtbot.waitUntil(lambda: vm.transcript == "Hola mundo", timeout=5000)
    request = requests(engine, "POST", "/v1/voices")[0]
    assert request["fields"] == {"name": "Mine", "language": "es"}
    assert request["files"]["audio"]["data"] == FakeRecorder.content
    assert not vm.transcribing and vm.take is None and not take.exists()
    qtbot.waitUntil(lambda: [v.voice_id for v in vm.voices] == ["V5"], timeout=5000)


def test_discard_take_deletes_the_file(make_vm, recorder):
    vm = make_vm()
    vm.start_recording()
    vm.stop_recording()
    take = vm.take
    vm.discard_take()
    assert vm.take is None and not take.exists()


def test_recorder_failure_is_reported(make_vm, recorder):
    vm = make_vm()
    vm.start_recording()
    recorder.failed.emit("No microphone found")
    assert not vm.recording and vm.error.code == "recording_failed" and vm.error.message == "No microphone found"


def test_generate_follows_events_plays_and_keeps_history(qtbot, engine, make_vm, player):
    engine.controller.voice_answers["POST /v1/speech-jobs"] = (202, make_job("S1"))
    engine.controller.voice_files["GET /v1/speech-jobs/S1/result"] = b"RIFF speech"
    engine.controller.push(progress("S1", 0.5, "Halfway"), completed("S1"), CLOSE)
    vm = make_vm()
    seen = []
    vm.changed.connect(lambda: seen.append((vm.progress, vm.message)))
    vm.generate("V1", "es", "Hola")
    qtbot.waitUntil(lambda: vm.last_clip is not None, timeout=10000)
    assert (0.5, "Halfway") in seen and not vm.generating
    assert requests(engine, "POST", "/v1/speech-jobs")[0]["json"] == {"voice_id": "V1", "language": "es", "text": "Hola"}
    assert player.played == [vm.last_clip] and vm.last_clip.read_bytes() == b"RIFF speech"
    assert vm.history("V1") == [vm.last_clip]
    assert vm.history("V2") == []


def test_history_survives_a_new_view_model(qtbot, engine, make_vm):
    engine.controller.voice_answers["POST /v1/speech-jobs"] = (202, make_job("S1"))
    engine.controller.voice_files["GET /v1/speech-jobs/S1/result"] = b"RIFF speech"
    engine.controller.push(completed("S1"), CLOSE)
    first = make_vm()
    first.generate("V1", "es", "Hola")
    qtbot.waitUntil(lambda: first.last_clip is not None, timeout=10000)
    assert make_vm().history("V1") == [first.last_clip]


def test_save_clip_copies_and_delete_clip_removes(qtbot, engine, make_vm, tmp_path):
    engine.controller.voice_answers["POST /v1/speech-jobs"] = (202, make_job("S1"))
    engine.controller.voice_files["GET /v1/speech-jobs/S1/result"] = b"RIFF speech"
    engine.controller.push(completed("S1"), CLOSE)
    vm = make_vm()
    vm.generate("V1", "es", "Hola")
    qtbot.waitUntil(lambda: vm.last_clip is not None, timeout=10000)
    target = tmp_path / "out.wav"
    vm.save_clip(target)
    assert target.read_bytes() == b"RIFF speech"
    clip = vm.last_clip
    vm.delete_clip(clip)
    assert not clip.exists() and vm.history("V1") == [] and vm.last_clip is None


def test_generate_api_error_surfaces_the_code(qtbot, engine, make_vm):
    engine.controller.voice_answers["POST /v1/speech-jobs"] = (404, error_body("voice_not_found", "No voice V1"))
    vm = make_vm()
    vm.generate("V1", "es", "Hola")
    qtbot.waitUntil(lambda: vm.error is not None, timeout=5000)
    assert (vm.error.code, vm.error.message) == ("voice_not_found", "No voice V1") and not vm.generating


def test_unavailable_voice_refuses_every_action(engine, make_vm, recorder):
    vm = make_vm(FakeSupervisor(voice=False, voice_reason="Voice model not installed"))
    assert not vm.available and vm.unavailable_reason == "Voice model not installed"
    vm.refresh()
    vm.preview("d", "en", "t")
    vm.generate("V1", "es", "Hola")
    vm.start_recording()
    vm.rename("V1", "x")
    vm.delete("V1")
    assert engine.controller.voice_requests == [] and not vm.recording and recorder.target is None


def test_engine_not_ready_is_unavailable(make_vm):
    vm = make_vm(FakeSupervisor(state=EngineState.loading, voice=True))
    assert not vm.available and not vm.engine_ready


def test_becoming_available_loads_the_voices(qtbot, engine, make_vm):
    script_voices(engine, voice_json("V1"))
    supervisor = FakeSupervisor(voice=False, voice_reason="loading")
    vm = make_vm(supervisor)
    supervisor.set_voice(True)
    qtbot.waitUntil(lambda: len(vm.voices) == 1, timeout=5000)


def test_saving_a_voice_emits_voice_saved_with_it(qtbot, engine, make_vm):
    engine.controller.voice_answers["POST /v1/voice-previews"] = (202, make_job("PV3"))
    engine.controller.voice_files["GET /v1/speech-jobs/PV3/result"] = b"RIFF preview"
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json("V9", "Deep"))
    script_voices(engine, voice_json("V9", "Deep"))
    engine.controller.push(completed("PV3"), CLOSE)
    vm = make_vm()
    vm.preview("d", "en", "t")
    qtbot.waitUntil(lambda: vm.preview_ready, timeout=10000)
    with qtbot.waitSignal(vm.voice_saved, timeout=5000) as blocker:
        vm.save_preview("Deep")
    assert blocker.args[0].voice_id == "V9"


def test_reset_draft_clears_preview_take_and_error(qtbot, engine, make_vm, recorder, player):
    vm = make_vm()
    vm.preview_id, vm.preview_path = "PV1", None
    vm.start_recording()
    vm.stop_recording()
    take = vm.take
    assert take is not None and take.exists()
    vm.transcript = "old"
    vm.reset_draft()
    assert vm.take is None and not take.exists() and vm.transcript is None
    assert not vm.preview_ready and vm.error is None and player.stops >= 1


def test_reset_draft_while_recording_discards_the_take(make_vm, recorder):
    vm = make_vm()
    vm.start_recording()
    vm.reset_draft()
    assert not vm.recording and vm.take is None
    assert recorder.target is not None and not recorder.target.exists()
