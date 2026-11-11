import time

import pytest
from fastapi.testclient import TestClient

from model3d_engine.api.app import create_app
from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.voices import Voice, VoiceKind, VoiceStore

from .fakes import FakeRunner
from .test_api import MODE, assert_error


def make(tmp_path) -> TestClient:
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, FakeRunner("shape.glb"), FakeRunner("model.glb"))
    app = create_app(
        settings, scheduler, store, bus,
        health=lambda: {"ready": scheduler.ready, "mode": MODE, "gpus": [], "rig": False, "rig_reason": None},
    )
    return TestClient(app, base_url="http://127.0.0.1:8765")


def make_voice(root, name: str, sample: bytes = b"RIFF-sample") -> Voice:
    return VoiceStore(root).create(name, VoiceKind.recorded, "es", f"transcript {name}", sample)


def test_list_is_empty_on_fresh_home(tmp_path):
    client = make(tmp_path)
    resp = client.get("/v1/voices")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_voices_newest_first_with_fields(tmp_path):
    client = make(tmp_path)
    root = tmp_path / "voices"
    make_voice(root, "a")
    time.sleep(1.1)
    make_voice(root, "b")
    body = client.get("/v1/voices").json()
    assert [v["name"] for v in body] == ["b", "a"]
    assert body[0]["kind"] == "recorded"
    assert body[0]["language"] == "es"
    assert body[0]["transcript"] == "transcript b"
    assert body[0]["voice_id"]
    assert body[0]["created_at"]


def test_get_one_voice_returns_its_fields(tmp_path):
    client = make(tmp_path)
    voice = make_voice(tmp_path / "voices", "solo")
    resp = client.get(f"/v1/voices/{voice.voice_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["voice_id"] == voice.voice_id
    assert body["name"] == "solo"
    assert body["kind"] == "recorded"
    assert body["language"] == "es"
    assert body["transcript"] == "transcript solo"


def test_sample_returns_the_exact_audio_bytes(tmp_path):
    client = make(tmp_path)
    voice = make_voice(tmp_path / "voices", "s", sample=b"RIFF-exact-bytes")
    resp = client.get(f"/v1/voices/{voice.voice_id}/sample")
    assert resp.status_code == 200
    assert resp.content == b"RIFF-exact-bytes"
    assert resp.headers["content-type"].startswith("audio/wav")


def test_rename_returns_stripped_name_and_list_shows_it(tmp_path):
    client = make(tmp_path)
    voice = make_voice(tmp_path / "voices", "old")
    resp = client.patch(f"/v1/voices/{voice.voice_id}", json={"name": "  New name  "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New name"
    assert [v["name"] for v in client.get("/v1/voices").json()] == ["New name"]


def test_rename_to_blank_name_is_rejected_and_keeps_name(tmp_path):
    client = make(tmp_path)
    voice = make_voice(tmp_path / "voices", "kept")
    resp = client.patch(f"/v1/voices/{voice.voice_id}", json={"name": "   "})
    assert_error(resp, 400, "invalid_name")
    assert client.get(f"/v1/voices/{voice.voice_id}").json()["name"] == "kept"


def test_delete_removes_folder_and_list_no_longer_has_it(tmp_path):
    client = make(tmp_path)
    voice = make_voice(tmp_path / "voices", "gone")
    resp = client.delete(f"/v1/voices/{voice.voice_id}")
    assert resp.status_code == 204
    assert resp.content == b""
    assert not (tmp_path / "voices" / voice.voice_id).exists()
    assert client.get("/v1/voices").json() == []


def test_unknown_id_is_not_found_on_every_route(tmp_path):
    client = make(tmp_path)
    missing = "20000101T000000-000000"
    assert_error(client.get(f"/v1/voices/{missing}"), 404, "voice_not_found")
    assert_error(client.get(f"/v1/voices/{missing}/sample"), 404, "voice_not_found")
    assert_error(client.patch(f"/v1/voices/{missing}", json={"name": "x"}), 404, "voice_not_found")
    assert_error(client.delete(f"/v1/voices/{missing}"), 404, "voice_not_found")


def test_malformed_ids_are_not_found_and_touch_nothing_outside_the_root(tmp_path):
    client = make(tmp_path)
    sentinel = tmp_path / "jobs" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep")
    assert_error(client.get("/v1/voices/bad.id"), 404, "voice_not_found")
    assert_error(client.get("/v1/voices/bad.id/sample"), 404, "voice_not_found")
    assert_error(client.patch("/v1/voices/bad.id", json={"name": "x"}), 404, "voice_not_found")
    assert_error(client.delete("/v1/voices/bad.id"), 404, "voice_not_found")
    # A decoded slash never matches the single-segment route, so the framework answers 404 before the handler.
    assert client.delete("/v1/voices/..%2F..%2Fjobs").status_code == 404
    assert sentinel.read_text() == "keep"


def test_corrupt_voice_is_skipped_and_good_voice_still_listed(tmp_path):
    root = tmp_path / "voices"
    good = make_voice(root, "good")
    broken = root / "20000101T000000-bad000"
    broken.mkdir()
    (broken / "voice.json").write_text("{not json")
    listed = VoiceStore(root).voices()
    assert [v.voice_id for v in listed] == [good.voice_id]


def test_create_with_blank_name_raises_and_leaves_no_folder(tmp_path):
    root = tmp_path / "voices"
    with pytest.raises(ValueError):
        VoiceStore(root).create("   ", VoiceKind.designed, "en", "hello", b"RIFF")
    assert not root.exists() or list(root.iterdir()) == []


def test_api_lists_the_voices_stored_under_settings_voices_dir(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    voice = VoiceStore(settings.voices_dir).create("shared", VoiceKind.designed, "en", "hi", b"RIFF")
    client = make(tmp_path)
    listed = client.get("/v1/voices").json()
    assert [v["voice_id"] for v in listed] == [voice.voice_id]
    assert settings.voices_dir == tmp_path / "voices"
    assert (tmp_path / "voices" / voice.voice_id).is_dir()
