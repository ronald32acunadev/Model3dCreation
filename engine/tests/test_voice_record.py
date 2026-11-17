from .fakes import FakeVoiceRunner
from .test_api import assert_error
from .test_speech import make

WAV = b"RIFFfake-clip"


def record(client, name="Me", language="es", filename="take.wav", data=WAV):
    files = {"audio": (filename, data, "application/octet-stream")}
    return client.post("/v1/voices", data={"name": name, "language": language}, files=files)


def only_the_seed_voice(client):
    return len(client.get("/v1/voices").json()) == 1


def test_recording_is_transcribed_and_saved_as_a_voice(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        response = record(client, name="  Me ", language="es", filename="Take.WAV")
        assert response.status_code == 201
        voice = response.json()
        assert voice["name"] == "Me"
        assert voice["kind"] == "recorded"
        assert voice["language"] == "es"
        assert voice["transcript"] == "Hola, soy yo."
        sample = client.get(f"/v1/voices/{voice['voice_id']}/sample").content
        assert sample[:4] == b"RIFF"
        assert sample != WAV
        assert len(client.get("/v1/voices").json()) == 2


def test_each_accepted_suffix_is_transcribed(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        for suffix in (".wav", ".mp3", ".ogg", ".flac"):
            assert record(client, filename=f"take{suffix}").status_code == 201


def test_short_clip_is_invalid_audio_with_the_limits(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        error = assert_error(record(client, data=b"RIFF short clip"), 400, "invalid_audio")
        assert error["message"] == "Recordings must be 3 to 60 seconds long"
        assert only_the_seed_voice(client)


def test_unsupported_suffix_is_invalid_audio_and_makes_no_job(tmp_path):
    client, store, _, _ = make(tmp_path)
    with client:
        assert_error(record(client, filename="take.aac"), 400, "invalid_audio")
        assert only_the_seed_voice(client)
    assert not store.jobs_dir.exists() or list(store.jobs_dir.iterdir()) == []


def test_missing_audio_is_invalid_audio(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        response = client.post(
            "/v1/voices", data={"name": "Me", "language": "es"}, files={"other": ("x.txt", b"x", "text/plain")}
        )
        assert_error(response, 400, "invalid_audio")
        assert only_the_seed_voice(client)


def test_blank_name_is_rejected(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        assert_error(record(client, name="  "), 400, "invalid_name")
        assert only_the_seed_voice(client)


def test_bad_language_is_rejected(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        assert_error(record(client, language="fr"), 400, "invalid_language")
        assert only_the_seed_voice(client)


def test_wrong_content_type_is_415(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        response = client.post("/v1/voices", content=b"name=Me", headers={"content-type": "text/plain"})
        assert_error(response, 415, "unsupported_media_type")
        assert only_the_seed_voice(client)


def test_oversize_upload_is_413(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        response = record(client, data=b"x" * (20 * 1024 * 1024 + 1))
        error = assert_error(response, 413, "audio_too_large")
        assert "20971520" in error["message"]
        assert only_the_seed_voice(client)


def test_voice_stage_missing_is_voice_unavailable(tmp_path):
    client, _, _, _ = make(tmp_path, voice=None)
    with client:
        assert_error(record(client), 400, "voice_unavailable")
        assert only_the_seed_voice(client)


def test_a_failed_transcription_is_reported_with_its_code(tmp_path):
    from model3d_engine.stages.protocol import StageError

    client, _, _, _ = make(tmp_path, voice=FakeVoiceRunner(fail_with=StageError("speech_failed", "Whisper failed")))
    with client:
        error = assert_error(record(client), 500, "speech_failed")
        assert error["message"] == "Whisper failed"
        assert only_the_seed_voice(client)
