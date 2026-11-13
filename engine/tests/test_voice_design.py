import json

from .fakes import FakeVoiceRunner
from .test_api import assert_error, wait_state
from .test_speech import make, speak

DESCRIPTION = "A warm, low female voice with a calm pace"
SAMPLE_TEXT = "Hola, esta es mi voz."


def preview(client, description=DESCRIPTION, language="es", sample_text=SAMPLE_TEXT):
    return client.post(
        "/v1/voice-previews", json={"description": description, "language": language, "sample_text": sample_text}
    )


def save(client, preview_id, name="Narrator"):
    return client.post("/v1/voices", json={"name": name, "kind": "designed", "preview_id": preview_id})


def test_preview_answers_202_with_a_speech_job_that_designs_the_voice(tmp_path):
    client, store, _, _ = make(tmp_path)
    with client:
        response = preview(client, description=f"  {DESCRIPTION}  ")
        assert response.status_code == 202
        job = response.json()
        assert job["kind"] == "speech"
        request = json.loads((store.get(job["job_id"]).dir / "speech_request.json").read_text())
        assert request == {
            "type": "design",
            "text": SAMPLE_TEXT,
            "language": "es",
            "ref_audio": None,
            "ref_text": None,
            "instruct": DESCRIPTION,
        }


def test_preview_result_is_a_wav_after_success(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        job_id = preview(client).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        result = client.get(f"/v1/speech-jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.headers["content-type"] == "audio/wav"
        assert result.content[:4] == b"RIFF"


def test_blank_or_long_description_is_rejected_and_no_job_is_made(tmp_path):
    client, store, _, _ = make(tmp_path)
    with client:
        for description in ("", "   ", "x" * 1001):
            assert_error(preview(client, description=description), 400, "invalid_description")
        assert preview(client, description="x" * 1000).status_code == 202
    assert len(list(store.jobs_dir.iterdir())) == 1


def test_blank_or_long_sample_text_is_rejected(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        for text in ("", "  ", "x" * 501):
            assert_error(preview(client, sample_text=text), 400, "invalid_text")
        assert preview(client, sample_text="x" * 500).status_code == 202


def test_voice_stage_missing_is_voice_unavailable_and_makes_no_job(tmp_path):
    client, store, _, _ = make(tmp_path, voice=None)
    with client:
        assert_error(preview(client), 400, "voice_unavailable")
    assert not store.jobs_dir.exists() or list(store.jobs_dir.iterdir()) == []


def test_saved_preview_becomes_a_designed_voice(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        preview_id = preview(client).json()["job_id"]
        wait_state(client, preview_id, "succeeded")
        wav = client.get(f"/v1/speech-jobs/{preview_id}/result").content

        response = save(client, preview_id, name="  Narrator ")
        assert response.status_code == 201
        voice = response.json()
        assert voice["name"] == "Narrator"
        assert voice["kind"] == "designed"
        assert voice["language"] == "es"
        assert voice["transcript"] == SAMPLE_TEXT
        assert client.get(f"/v1/voices/{voice['voice_id']}/sample").content == wav
        assert voice["voice_id"] in [item["voice_id"] for item in client.get("/v1/voices").json()]


def test_speech_with_the_designed_voice_clones_its_sample(tmp_path):
    client, store, _, _ = make(tmp_path)
    with client:
        preview_id = preview(client).json()["job_id"]
        wait_state(client, preview_id, "succeeded")
        voice = save(client, preview_id).json()

        job_id = speak(client, voice["voice_id"]).json()["job_id"]
        request = json.loads((store.get(job_id).dir / "speech_request.json").read_text())
        sample = store.jobs_dir.parent / "voices" / voice["voice_id"] / "sample.wav"
        assert request["type"] == "speak"
        assert request["ref_audio"] == str(sample.resolve())
        assert request["ref_text"] == SAMPLE_TEXT
        assert request["instruct"] is None


def test_unknown_preview_is_not_found(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        assert_error(save(client, "nope"), 404, "preview_not_found")
        assert len(client.get("/v1/voices").json()) == 1


def test_a_speak_job_is_not_a_preview(tmp_path):
    client, _, _, voice = make(tmp_path)
    with client:
        job_id = speak(client, voice.voice_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        assert_error(save(client, job_id), 404, "preview_not_found")
        assert len(client.get("/v1/voices").json()) == 1


def test_unfinished_preview_is_not_finished(tmp_path):
    client, _, _, _ = make(tmp_path, voice=FakeVoiceRunner(delay=0.5))
    with client:
        preview_id = preview(client).json()["job_id"]
        assert_error(save(client, preview_id), 409, "preview_not_finished")
        assert len(client.get("/v1/voices").json()) == 1


def test_blank_name_is_rejected_and_saves_nothing(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        preview_id = preview(client).json()["job_id"]
        wait_state(client, preview_id, "succeeded")
        assert_error(save(client, preview_id, name="   "), 400, "invalid_name")
        assert len(client.get("/v1/voices").json()) == 1


def test_a_malformed_save_body_is_invalid_request(tmp_path):
    client, _, _, _ = make(tmp_path)
    with client:
        response = client.post("/v1/voices", json={"name": "A", "kind": "recorded", "preview_id": "x"})
        assert_error(response, 400, "invalid_request")
        response = client.post("/v1/voices", content=b"{", headers={"content-type": "application/json"})
        assert_error(response, 400, "invalid_request")
