import json
import logging
import shutil

from model3d_contracts.library import Library
from model3d_engine.api.app import create_app
from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.voices import VoiceKind, VoiceStore

from .fakes import FakeRunner, FakeVoiceRunner
from .test_api import MODE, ReadyClient, assert_error, wait_state
from .test_library import add_entry
from .test_speech import UNAVAILABLE, make

TEXT = "Hola mundo. Adios."
LAN_URL = "http://192.168.90.10:8765"
UNAUTHORIZED = "Send Authorization: Bearer <key> to use the engine from the network"


def setup(tmp_path, **kwargs):
    client, store, scheduler, voice = make(tmp_path, **kwargs)
    library = Library(tmp_path / "library", recover=False)
    hero = add_entry(library, tmp_path, "hero")
    return client, store, scheduler, voice, library, hero


def job_ids(tmp_path):
    jobs = tmp_path / "jobs"
    return sorted(path.name for path in jobs.iterdir()) if jobs.exists() else []


def link(client, character_id, voice_id):
    return client.put(f"/v1/characters/{character_id}/voice", json={"voice_id": voice_id})


def say(client, character_id, text=TEXT, language="es"):
    return client.post(f"/v1/characters/{character_id}/speech", json={"text": text, "language": language})


def test_link_shows_voice_id_and_unlink_clears_it(tmp_path):
    client, _, _, voice, _, hero = setup(tmp_path)
    with client:
        assert client.get(f"/v1/characters/{hero.entry_id}").json()["voice_id"] is None

        linked = link(client, hero.entry_id, voice.voice_id)

        assert linked.status_code == 200
        assert linked.json()["voice_id"] == voice.voice_id
        assert client.get(f"/v1/characters/{hero.entry_id}").json()["voice_id"] == voice.voice_id
        assert client.delete(f"/v1/characters/{hero.entry_id}/voice").status_code == 204
        assert client.get(f"/v1/characters/{hero.entry_id}").json()["voice_id"] is None
        assert client.delete(f"/v1/characters/{hero.entry_id}/voice").status_code == 204


def test_one_voice_serves_two_characters(tmp_path):
    client, _, _, voice, library, hero = setup(tmp_path)
    villain = add_entry(library, tmp_path, "villain")
    with client:
        link(client, hero.entry_id, voice.voice_id)
        link(client, villain.entry_id, voice.voice_id)

        assert client.get(f"/v1/characters/{hero.entry_id}").json()["voice_id"] == voice.voice_id
        assert client.get(f"/v1/characters/{villain.entry_id}").json()["voice_id"] == voice.voice_id


def test_unknown_voice_or_character_is_404_on_link(tmp_path):
    client, _, _, voice, _, hero = setup(tmp_path)
    with client:
        error = assert_error(link(client, hero.entry_id, "nope"), 404, "voice_not_found")
        assert error["message"] == "No voice nope"
        assert client.get(f"/v1/characters/{hero.entry_id}").json()["voice_id"] is None
        assert_error(link(client, "missing", voice.voice_id), 404, "character_not_found")
        assert_error(client.delete("/v1/characters/missing/voice"), 404, "character_not_found")


def test_speech_job_uses_the_linked_voice_and_keeps_the_phrase(tmp_path):
    client, store, _, voice, library, hero = setup(tmp_path)
    with client:
        link(client, hero.entry_id, voice.voice_id)
        response = say(client, hero.entry_id, text="  Hello there.  ", language="en")

        assert response.status_code == 202
        job = response.json()
        assert job["kind"] == "speech"
        assert job["links"]["result"] == f"/v1/speech-jobs/{job['job_id']}/result"
        wait_state(client, job["job_id"], "succeeded")
        request = json.loads((tmp_path / "jobs" / job["job_id"] / "speech_request.json").read_text())
        assert request == {
            "type": "speak",
            "text": "Hello there.",
            "language": "en",
            "ref_audio": str((tmp_path / "voices" / voice.voice_id / "sample.wav").resolve()),
            "ref_text": "Hola, soy Ana.",
            "instruct": None,
            "character_id": hero.entry_id,
        }
        wav = client.get(f"/v1/speech-jobs/{job['job_id']}/result").content

        phrases = client.get(f"/v1/characters/{hero.entry_id}/phrases").json()
        assert [(p["phrase_id"], p["text"], p["language"]) for p in phrases] == [(job["job_id"], "Hello there.", "en")]
        assert phrases[0]["created_at"]
        assert client.get(f"/v1/characters/{hero.entry_id}").json()["phrase_count"] == 1
        audio = client.get(f"/v1/characters/{hero.entry_id}/phrases/{job['job_id']}/audio")
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content == wav

        assert client.delete(f"/v1/characters/{hero.entry_id}/phrases/{job['job_id']}").status_code == 204
        assert client.get(f"/v1/characters/{hero.entry_id}/phrases").json() == []
        assert_error(
            client.delete(f"/v1/characters/{hero.entry_id}/phrases/{job['job_id']}"), 404, "phrase_not_found"
        )
        assert_error(
            client.get(f"/v1/characters/{hero.entry_id}/phrases/{job['job_id']}/audio"), 404, "phrase_not_found"
        )


def test_plain_speech_job_keeps_no_phrase(tmp_path):
    client, _, _, voice, library, hero = setup(tmp_path)
    with client:
        link(client, hero.entry_id, voice.voice_id)
        job_id = client.post(
            "/v1/speech-jobs", json={"voice_id": voice.voice_id, "language": "es", "text": TEXT}
        ).json()["job_id"]
        wait_state(client, job_id, "succeeded")

        assert client.get(f"/v1/characters/{hero.entry_id}/phrases").json() == []
        assert not (library.folder(hero.entry_id) / "phrases").exists()


def test_unknown_phrase_and_unknown_character_on_phrase_routes(tmp_path):
    client, _, _, _, _, hero = setup(tmp_path)
    with client:
        assert client.get(f"/v1/characters/{hero.entry_id}/phrases").json() == []
        assert_error(client.get(f"/v1/characters/{hero.entry_id}/phrases/nope/audio"), 404, "phrase_not_found")
        assert_error(client.delete(f"/v1/characters/{hero.entry_id}/phrases/nope"), 404, "phrase_not_found")
        assert_error(client.get("/v1/characters/missing/phrases"), 404, "character_not_found")
        assert_error(client.get("/v1/characters/missing/phrases/p/audio"), 404, "character_not_found")


def test_speech_without_a_voice_is_409(tmp_path):
    client, store, _, _, _, hero = setup(tmp_path)
    with client:
        error = assert_error(say(client, hero.entry_id), 409, "voice_not_assigned")
        assert error["message"] == f"Character {hero.entry_id} has no voice"
        assert job_ids(tmp_path) == []


def test_deleting_the_voice_clears_the_link_and_keeps_phrases(tmp_path):
    client, _, _, voice, library, hero = setup(tmp_path)
    other = add_entry(library, tmp_path, "other")
    with client:
        link(client, hero.entry_id, voice.voice_id)
        link(client, other.entry_id, voice.voice_id)
        job_id = say(client, hero.entry_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")

        assert client.delete(f"/v1/voices/{voice.voice_id}").status_code == 204

        assert client.get(f"/v1/characters/{hero.entry_id}").json()["voice_id"] is None
        assert client.get(f"/v1/characters/{other.entry_id}").json()["voice_id"] is None
        assert_error(say(client, hero.entry_id), 409, "voice_not_assigned")
        assert [p["phrase_id"] for p in client.get(f"/v1/characters/{hero.entry_id}/phrases").json()] == [job_id]


def test_link_to_a_voice_removed_behind_the_api_is_409(tmp_path):
    client, _, _, voice, _, hero = setup(tmp_path)
    with client:
        link(client, hero.entry_id, voice.voice_id)
        shutil.rmtree(tmp_path / "voices" / voice.voice_id)

        assert_error(say(client, hero.entry_id), 409, "voice_not_assigned")


def test_blank_and_long_text_are_invalid_text(tmp_path):
    client, _, _, voice, _, hero = setup(tmp_path)
    with client:
        link(client, hero.entry_id, voice.voice_id)
        assert_error(say(client, hero.entry_id, text="   "), 400, "invalid_text")
        assert_error(say(client, hero.entry_id, text="a" * 2001), 400, "invalid_text")
        assert job_ids(tmp_path) == []


def test_text_is_checked_before_the_voice_assignment(tmp_path):
    client, _, _, _, _, hero = setup(tmp_path)
    with client:
        assert_error(say(client, hero.entry_id, text=" "), 400, "invalid_text")


def test_missing_voice_stage_is_voice_unavailable_and_creates_no_job(tmp_path):
    client, _, _, voice, _, hero = setup(tmp_path, voice=None)
    with client:
        link(client, hero.entry_id, voice.voice_id)

        error = assert_error(say(client, hero.entry_id), 400, "voice_unavailable")

        assert error["message"] == UNAVAILABLE
        assert job_ids(tmp_path) == []


def test_derived_entry_is_not_a_character_on_every_route(tmp_path):
    client, _, _, voice, library, hero = setup(tmp_path)
    rigged = library.create_derived(hero.entry_id)
    base = f"/v1/characters/{rigged.entry_id}"
    with client:
        assert_error(link(client, rigged.entry_id, voice.voice_id), 404, "character_not_found")
        assert_error(client.delete(f"{base}/voice"), 404, "character_not_found")
        assert_error(say(client, rigged.entry_id), 404, "character_not_found")
        assert_error(client.get(f"{base}/phrases"), 404, "character_not_found")
        assert_error(client.get(f"{base}/phrases/p/audio"), 404, "character_not_found")
        assert_error(client.delete(f"{base}/phrases/p"), 404, "character_not_found")


def test_speech_for_unknown_character_is_404(tmp_path):
    client, _, _, _, _, _ = setup(tmp_path)
    with client:
        assert_error(say(client, "missing"), 404, "character_not_found")


def lan_setup(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", lan_host="192.168.90.10", api_keys=("k1",))
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, FakeRunner("shape.glb"), FakeRunner("model.glb"), voice=FakeVoiceRunner())
    app = create_app(
        settings, scheduler, store, bus,
        health=lambda: {"ready": scheduler.ready, "mode": MODE, "gpus": [], "voice": True, "voice_reason": None},
        voice_reason=lambda: None,
    )
    client = ReadyClient(app, base_url=LAN_URL)
    client.wait_ready = False
    voice = VoiceStore(settings.voices_dir).create("Ana", VoiceKind.recorded, "es", "Hola, soy Ana.", b"RIFFsample")
    hero = add_entry(Library(tmp_path / "library", recover=False), tmp_path, "hero")
    return client, voice, hero


def test_lan_client_needs_the_key_on_the_character_endpoints(tmp_path):
    client, voice, hero = lan_setup(tmp_path)
    base = f"/v1/characters/{hero.entry_id}"
    with client:
        auth = {"Authorization": "Bearer k1"}
        speech = {"text": TEXT, "language": "es"}
        error = assert_error(client.post(f"{base}/speech", json=speech), 401, "unauthorized")
        assert error["message"] == UNAUTHORIZED
        assert_error(client.put(f"{base}/voice", json={"voice_id": voice.voice_id}), 401, "unauthorized")
        assert_error(client.get(f"{base}/phrases"), 401, "unauthorized")
        assert_error(client.delete(f"{base}/phrases/x"), 401, "unauthorized")

        assert client.put(f"{base}/voice", json={"voice_id": voice.voice_id}, headers=auth).status_code == 200
        response = client.post(f"{base}/speech", json=speech, headers=auth)
        assert response.status_code == 202
        assert response.json()["kind"] == "speech"


def test_localhost_needs_no_key_for_character_speech(tmp_path):
    client, _, _, voice, _, hero = setup(tmp_path)
    with client:
        link(client, hero.entry_id, voice.voice_id)
        assert say(client, hero.entry_id).status_code == 202


def test_a_failing_hook_does_not_fail_the_job(tmp_path, caplog):
    client, _, scheduler, voice, _, hero = setup(tmp_path)

    def broken(record):
        raise RuntimeError("hook boom")

    scheduler.on_speech_succeeded = broken
    with client, caplog.at_level(logging.ERROR):
        link(client, hero.entry_id, voice.voice_id)
        job_id = say(client, hero.entry_id).json()["job_id"]

        wait_state(client, job_id, "succeeded")

        assert client.get(f"/v1/speech-jobs/{job_id}/result").status_code == 200
        assert "Speech hook failed" in caplog.text
        assert client.get(f"/v1/characters/{hero.entry_id}/phrases").json() == []


def test_phrase_of_a_character_deleted_meanwhile_is_skipped(tmp_path, caplog):
    client, store, scheduler, voice, library, hero = setup(tmp_path)
    with client:
        link(client, hero.entry_id, voice.voice_id)
        job_id = say(client, hero.entry_id).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        shutil.rmtree(library.folder(hero.entry_id))

        with caplog.at_level(logging.WARNING):
            scheduler.on_speech_succeeded(store.get(job_id))

        assert f"Character {hero.entry_id} no longer exists" in caplog.text
