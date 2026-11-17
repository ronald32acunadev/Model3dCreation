from datetime import UTC, datetime

import pytest
from model3d_contracts.characters import Character, Phrase
from model3d_contracts.jobs import Job
from model3d_contracts.voices import Voice, VoiceKind

from fake_engine import FakeEngine, error_body, make_job
from model3d_client.services.engine_api import ApiError


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


def call(qtbot, method, *args):
    results = []
    method(*args, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    return results[0]


def voice_json(voice_id="V1", name="Narrator", kind="designed", language="es") -> dict:
    return {"voice_id": voice_id, "name": name, "kind": kind, "language": language,
            "created_at": "2026-11-01T12:00:00Z", "transcript": "Hola"}


def character_json(character_id="C1", voice_id=None) -> dict:
    return {"character_id": character_id, "name": "Pingu", "created_at": "2026-11-01T12:00:00Z",
            "input_file": "input.png", "versions": [character_id, "C2"], "voice_id": voice_id, "phrase_count": 2}


def phrase_json(phrase_id="P1") -> dict:
    return {"phrase_id": phrase_id, "text": "Hello there", "language": "en", "created_at": "2026-11-01T13:00:00Z"}


def last(engine) -> dict:
    return engine.controller.voice_requests[-1]


def test_voices_lists_parsed_voices(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/voices"] = (200, [voice_json("V1"), voice_json("V2", "Hero", "recorded", "en")])
    voices = call(qtbot, make_api(engine.url).voices)
    assert [v.voice_id for v in voices] == ["V1", "V2"]
    assert voices[1] == Voice(voice_id="V2", name="Hero", kind=VoiceKind.recorded, language="en",
                              created_at=datetime(2026, 11, 1, 12, tzinfo=UTC), transcript="Hola")
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/voices")


def test_voices_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/voices"] = (500, error_body("voices_unreadable"))
    assert call(qtbot, make_api(engine.url).voices).code == "voices_unreadable"


def test_voices_unreachable_engine(qtbot, make_api):
    error = call(qtbot, make_api("http://127.0.0.1:9").voices)
    assert (error.status, error.code) == (None, "engine_unreachable")


def test_voices_unreadable_answer_is_internal_error(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/voices"] = (200, [{"voice_id": "V1"}])
    error = call(qtbot, make_api(engine.url).voices)
    assert (error.status, error.code) == (200, "internal_error")


def test_rename_voice_patches_the_name(qtbot, engine, make_api):
    engine.controller.voice_answers["PATCH /v1/voices/V1"] = (200, voice_json(name="Hero"))
    voice = call(qtbot, make_api(engine.url).rename_voice, "V1", "Hero")
    assert voice.name == "Hero"
    assert (last(engine)["method"], last(engine)["path"], last(engine)["json"]) == ("PATCH", "/v1/voices/V1", {"name": "Hero"})


def test_rename_voice_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["PATCH /v1/voices/V1"] = (404, error_body("voice_not_found"))
    assert call(qtbot, make_api(engine.url).rename_voice, "V1", "Hero").code == "voice_not_found"


def test_delete_voice_gives_none(qtbot, engine, make_api):
    engine.controller.voice_answers["DELETE /v1/voices/V1"] = (204, None)
    assert call(qtbot, make_api(engine.url).delete_voice, "V1") is None
    assert (last(engine)["method"], last(engine)["path"]) == ("DELETE", "/v1/voices/V1")


def test_delete_voice_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["DELETE /v1/voices/V1"] = (404, error_body("voice_not_found"))
    assert call(qtbot, make_api(engine.url).delete_voice, "V1").code == "voice_not_found"


def test_download_voice_sample_writes_the_bytes(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_files["GET /v1/voices/V1/sample"] = b"RIFF sample"
    target = tmp_path / "sample.wav"
    assert call(qtbot, make_api(engine.url).download_voice_sample, "V1", target) == target
    assert target.read_bytes() == b"RIFF sample"
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/voices/V1/sample")


def test_download_voice_sample_error_code(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_answers["GET /v1/voices/V1/sample"] = (404, error_body("sample_not_found"))
    target = tmp_path / "sample.wav"
    assert call(qtbot, make_api(engine.url).download_voice_sample, "V1", target).code == "sample_not_found"
    assert not target.exists()


def test_preview_voice_posts_the_description(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/voice-previews"] = (202, make_job("PV1"))
    job = call(qtbot, make_api(engine.url).preview_voice, "a deep voice", "en", "Hello")
    assert isinstance(job, Job) and job.job_id == "PV1"
    assert (last(engine)["method"], last(engine)["path"]) == ("POST", "/v1/voice-previews")
    assert last(engine)["json"] == {"description": "a deep voice", "language": "en", "sample_text": "Hello"}


def test_preview_voice_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/voice-previews"] = (503, error_body("engine_not_ready"))
    assert call(qtbot, make_api(engine.url).preview_voice, "d", "en", "t").code == "engine_not_ready"


def test_save_designed_voice_posts_the_preview(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json("V9", "Deep"))
    voice = call(qtbot, make_api(engine.url).save_designed_voice, "Deep", "PV1")
    assert (voice.voice_id, voice.name) == ("V9", "Deep")
    assert (last(engine)["method"], last(engine)["path"]) == ("POST", "/v1/voices")
    assert last(engine)["json"] == {"name": "Deep", "kind": "designed", "preview_id": "PV1"}


def test_save_designed_voice_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/voices"] = (404, error_body("preview_not_found"))
    assert call(qtbot, make_api(engine.url).save_designed_voice, "Deep", "PV1").code == "preview_not_found"


def test_save_recorded_voice_uploads_the_audio(qtbot, engine, make_api, tmp_path):
    audio = tmp_path / "me.wav"
    audio.write_bytes(b"RIFF recording")
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json("V5", "Me", "recorded"))
    voice = call(qtbot, make_api(engine.url).save_recorded_voice, "Me", "es", audio)
    assert (voice.voice_id, voice.kind) == ("V5", VoiceKind.recorded)
    sent = last(engine)
    assert (sent["method"], sent["path"]) == ("POST", "/v1/voices")
    assert sent["fields"] == {"name": "Me", "language": "es"}
    assert sent["files"] == {"audio": {"filename": "me.wav", "content_type": "audio/wav", "data": b"RIFF recording"}}


def test_save_recorded_voice_error_code(qtbot, engine, make_api, tmp_path):
    audio = tmp_path / "me.wav"
    audio.write_bytes(b"x")
    engine.controller.voice_answers["POST /v1/voices"] = (422, error_body("audio_unreadable"))
    assert call(qtbot, make_api(engine.url).save_recorded_voice, "Me", "es", audio).code == "audio_unreadable"


def test_speak_posts_the_text(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/speech-jobs"] = (202, make_job("S1"))
    job = call(qtbot, make_api(engine.url).speak, "V1", "es", "Hola")
    assert job.job_id == "S1"
    assert (last(engine)["method"], last(engine)["path"]) == ("POST", "/v1/speech-jobs")
    assert last(engine)["json"] == {"voice_id": "V1", "language": "es", "text": "Hola"}


def test_speak_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/speech-jobs"] = (404, error_body("voice_not_found"))
    assert call(qtbot, make_api(engine.url).speak, "V1", "es", "Hola").code == "voice_not_found"


def test_download_speech_writes_the_bytes(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_files["GET /v1/speech-jobs/S1/result"] = b"RIFF speech"
    target = tmp_path / "speech.wav"
    assert call(qtbot, make_api(engine.url).download_speech, "S1", target) == target
    assert target.read_bytes() == b"RIFF speech"
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/speech-jobs/S1/result")


def test_download_speech_error_code(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_answers["GET /v1/speech-jobs/S1/result"] = (409, error_body("job_not_finished"))
    assert call(qtbot, make_api(engine.url).download_speech, "S1", tmp_path / "s.wav").code == "job_not_finished"


def test_characters_lists_parsed_characters(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/characters"] = (200, [character_json("C1", "V1"), character_json("C3")])
    characters = call(qtbot, make_api(engine.url).characters)
    assert [c.character_id for c in characters] == ["C1", "C3"]
    assert characters[0] == Character(character_id="C1", name="Pingu", created_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
                                      input_file="input.png", versions=["C1", "C2"], voice_id="V1", phrase_count=2)
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/characters")


def test_characters_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/characters"] = (500, error_body("library_unreadable"))
    assert call(qtbot, make_api(engine.url).characters).code == "library_unreadable"


def test_character_gets_one(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/characters/C1"] = (200, character_json("C1", "V1"))
    character = call(qtbot, make_api(engine.url).character, "C1")
    assert (character.character_id, character.voice_id, character.phrase_count) == ("C1", "V1", 2)
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/characters/C1")


def test_character_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/characters/C1"] = (404, error_body("character_not_found"))
    assert call(qtbot, make_api(engine.url).character, "C1").code == "character_not_found"


def test_set_character_voice_puts_the_voice(qtbot, engine, make_api):
    engine.controller.voice_answers["PUT /v1/characters/C1/voice"] = (200, character_json("C1", "V1"))
    assert call(qtbot, make_api(engine.url).set_character_voice, "C1", "V1") is None
    assert (last(engine)["method"], last(engine)["path"], last(engine)["json"]) == (
        "PUT", "/v1/characters/C1/voice", {"voice_id": "V1"})


def test_set_character_voice_none_deletes_the_link(qtbot, engine, make_api):
    engine.controller.voice_answers["DELETE /v1/characters/C1/voice"] = (204, None)
    assert call(qtbot, make_api(engine.url).set_character_voice, "C1", None) is None
    assert (last(engine)["method"], last(engine)["path"]) == ("DELETE", "/v1/characters/C1/voice")


def test_set_character_voice_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["PUT /v1/characters/C1/voice"] = (404, error_body("voice_not_found"))
    assert call(qtbot, make_api(engine.url).set_character_voice, "C1", "V1").code == "voice_not_found"


def test_character_speak_posts_the_text(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/characters/C1/speech"] = (202, make_job("S2"))
    job = call(qtbot, make_api(engine.url).character_speak, "C1", "Hello", "en")
    assert job.job_id == "S2"
    assert (last(engine)["method"], last(engine)["path"], last(engine)["json"]) == (
        "POST", "/v1/characters/C1/speech", {"text": "Hello", "language": "en"})


def test_character_speak_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["POST /v1/characters/C1/speech"] = (409, error_body("no_voice"))
    assert call(qtbot, make_api(engine.url).character_speak, "C1", "Hello", "en").code == "no_voice"


def test_phrases_lists_parsed_phrases(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/characters/C1/phrases"] = (200, [phrase_json("P1"), phrase_json("P2")])
    phrases = call(qtbot, make_api(engine.url).phrases, "C1")
    assert phrases[0] == Phrase(phrase_id="P1", text="Hello there", language="en",
                                created_at=datetime(2026, 11, 1, 13, tzinfo=UTC))
    assert [p.phrase_id for p in phrases] == ["P1", "P2"]
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/characters/C1/phrases")


def test_phrases_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["GET /v1/characters/C1/phrases"] = (404, error_body("character_not_found"))
    assert call(qtbot, make_api(engine.url).phrases, "C1").code == "character_not_found"


def test_download_phrase_writes_the_bytes(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_files["GET /v1/characters/C1/phrases/P1/audio"] = b"RIFF phrase"
    target = tmp_path / "phrase.wav"
    assert call(qtbot, make_api(engine.url).download_phrase, "C1", "P1", target) == target
    assert target.read_bytes() == b"RIFF phrase"
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/characters/C1/phrases/P1/audio")


def test_download_phrase_error_code(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_answers["GET /v1/characters/C1/phrases/P1/audio"] = (404, error_body("phrase_not_found"))
    assert call(qtbot, make_api(engine.url).download_phrase, "C1", "P1", tmp_path / "p.wav").code == "phrase_not_found"


def test_delete_phrase_gives_none(qtbot, engine, make_api):
    engine.controller.voice_answers["DELETE /v1/characters/C1/phrases/P1"] = (204, None)
    assert call(qtbot, make_api(engine.url).delete_phrase, "C1", "P1") is None
    assert (last(engine)["method"], last(engine)["path"]) == ("DELETE", "/v1/characters/C1/phrases/P1")


def test_delete_phrase_error_code(qtbot, engine, make_api):
    engine.controller.voice_answers["DELETE /v1/characters/C1/phrases/P1"] = (404, error_body("phrase_not_found"))
    assert call(qtbot, make_api(engine.url).delete_phrase, "C1", "P1").code == "phrase_not_found"


def test_export_character_writes_the_package(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_files["GET /v1/characters/C1/package"] = b"PK package"
    target = tmp_path / "pingu.m3dchar"
    assert call(qtbot, make_api(engine.url).export_character, "C1", target) == target
    assert target.read_bytes() == b"PK package"
    assert (last(engine)["method"], last(engine)["path"]) == ("GET", "/v1/characters/C1/package")


def test_export_character_error_code(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_answers["GET /v1/characters/C1/package"] = (404, error_body("character_not_found"))
    assert call(qtbot, make_api(engine.url).export_character, "C1", tmp_path / "p.m3dchar").code == "character_not_found"


def test_import_character_uploads_the_package(qtbot, engine, make_api, tmp_path):
    package = tmp_path / "pingu.m3dchar"
    package.write_bytes(b"PK package")
    engine.controller.voice_answers["POST /v1/characters/import"] = (201, character_json("C7"))
    character = call(qtbot, make_api(engine.url).import_character, package)
    assert character.character_id == "C7"
    sent = last(engine)
    assert (sent["method"], sent["path"]) == ("POST", "/v1/characters/import")
    assert sent["fields"] == {}
    assert sent["files"] == {"package": {"filename": "pingu.m3dchar", "content_type": "application/zip",
                                         "data": b"PK package"}}


def test_import_character_error_code(qtbot, engine, make_api, tmp_path):
    package = tmp_path / "bad.m3dchar"
    package.write_bytes(b"nope")
    engine.controller.voice_answers["POST /v1/characters/import"] = (422, error_body("invalid_package"))
    assert call(qtbot, make_api(engine.url).import_character, package).code == "invalid_package"
