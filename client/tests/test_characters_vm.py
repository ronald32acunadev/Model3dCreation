import json
import pytest
from model3d_contracts.characters import CHARACTER_FILE
from model3d_contracts.jobs import JobOptions

from fake_engine import CLOSE, FakeEngine, error_body, make_job
from model3d_client.services.library import Library
from model3d_client.viewmodels.characters import CharacterViewModel, export_character, import_character

TS = "2026-11-01T12:00:00Z"


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, path):
        self.played.append(path)


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


@pytest.fixture
def library(tmp_path, png):
    library = Library(tmp_path / "library")
    root = library.create(png, JobOptions())
    (library.folder(root.entry_id) / CHARACTER_FILE).write_text(json.dumps({"voice_id": "V1"}))
    return library


@pytest.fixture
def root_id(library):
    return library.entries()[0].entry_id


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def make_vm(make_api, engine, library, root_id, player, tmp_path):
    def make():
        return CharacterViewModel(make_api(engine.url), library, root_id, player, tmp_path / "cache")

    return make


def voice_json(voice_id="V1", name="Narrator"):
    return {"voice_id": voice_id, "name": name, "kind": "designed", "language": "es", "created_at": TS,
            "transcript": "Hola"}


def phrase_json(phrase_id="S1", text="Hello there"):
    return {"phrase_id": phrase_id, "text": text, "language": "en", "created_at": TS}


def requests(engine):
    return [(r["method"], r["path"]) for r in engine.controller.voice_requests]


def test_vm_reads_character_from_library(make_vm, root_id):
    vm = make_vm()
    assert (vm.character.character_id, vm.voice_id) == (root_id, "V1")


def test_refresh_loads_voices_and_phrases(qtbot, engine, make_vm, root_id):
    engine.controller.voice_answers["GET /v1/voices"] = (200, [voice_json("V1"), voice_json("V2", "Hero")])
    engine.controller.voice_answers[f"GET /v1/characters/{root_id}/phrases"] = (200, [phrase_json("P1")])
    vm = make_vm()
    vm.refresh()
    qtbot.waitUntil(lambda: len(vm.voices) == 2 and len(vm.phrases) == 1, timeout=5000)
    assert [v.name for v in vm.voices] == ["Narrator", "Hero"]
    assert vm.phrases[0].phrase_id == "P1"


def test_set_voice_puts_the_voice_id(qtbot, engine, make_vm, root_id):
    engine.controller.voice_answers[f"PUT /v1/characters/{root_id}/voice"] = (200, {})
    vm = make_vm()
    with qtbot.waitSignal(vm.changed, timeout=5000):
        vm.set_voice("V2")
    sent = engine.controller.voice_requests[-1]
    assert (sent["method"], sent["path"], sent["json"]) == ("PUT", f"/v1/characters/{root_id}/voice", {"voice_id": "V2"})
    assert vm.voice_id == "V2"


def test_clear_voice_sends_delete(qtbot, engine, make_vm, root_id):
    engine.controller.voice_answers[f"DELETE /v1/characters/{root_id}/voice"] = (204, None)
    vm = make_vm()
    with qtbot.waitSignal(vm.changed, timeout=5000):
        vm.set_voice(None)
    assert requests(engine)[-1] == ("DELETE", f"/v1/characters/{root_id}/voice")
    assert vm.voice_id is None


def test_say_follows_the_job_then_plays_the_new_phrase(qtbot, engine, make_vm, root_id, player, tmp_path):
    answers = engine.controller.voice_answers
    answers[f"POST /v1/characters/{root_id}/speech"] = (202, make_job("S1"))
    answers[f"GET /v1/characters/{root_id}/phrases"] = (200, [phrase_json("S1", "Hello there")])
    engine.controller.voice_files[f"GET /v1/characters/{root_id}/phrases/S1/audio"] = b"RIFF speech"
    engine.controller.push(
        {"type": "progress", "job_id": "S1", "ts": TS, "stage": "voice", "progress": 0.5, "message": "Speaking"},
        {"type": "completed", "job_id": "S1", "ts": TS, "result_url": "/v1/speech-jobs/S1/result"}, CLOSE)
    vm = make_vm()
    progress = []
    vm.changed.connect(lambda: progress.append((vm.progress, vm.message)))
    vm.say("Hello there", "en")
    assert vm.busy
    qtbot.waitUntil(lambda: bool(player.played), timeout=10000)
    posted = next(r for r in engine.controller.voice_requests if r["method"] == "POST")
    assert posted["json"] == {"text": "Hello there", "language": "en"}
    assert (0.5, "Speaking") in progress
    cached = tmp_path / "cache" / f"{root_id}-S1.wav"
    assert player.played == [cached]
    assert cached.read_bytes() == b"RIFF speech"
    assert [p.phrase_id for p in vm.phrases] == ["S1"]
    assert not vm.busy and vm.error is None


def test_say_failure_surfaces_the_job_error(qtbot, engine, make_vm, root_id):
    engine.controller.voice_answers[f"POST /v1/characters/{root_id}/speech"] = (202, make_job("S1"))
    engine.controller.push(
        {"type": "failed", "job_id": "S1", "ts": TS, "error": {"code": "tts_failed", "message": "Out of memory"}}, CLOSE)
    vm = make_vm()
    vm.say("Hi", "en")
    qtbot.waitUntil(lambda: vm.error is not None, timeout=10000)
    assert (vm.error.code, vm.error.message, vm.busy) == ("tts_failed", "Out of memory", False)


def test_voice_not_assigned_shows_a_friendly_message(qtbot, engine, make_vm, root_id):
    engine.controller.voice_answers[f"POST /v1/characters/{root_id}/speech"] = (409, error_body("voice_not_assigned"))
    vm = make_vm()
    vm.say("Hi", "en")
    qtbot.waitUntil(lambda: vm.error is not None, timeout=5000)
    assert vm.error.code == "voice_not_assigned"
    assert vm.error.message == "Pick a voice for this character first"
    assert not vm.busy


def test_say_ignores_empty_text_and_missing_voice(engine, make_vm, library, root_id):
    vm = make_vm()
    vm.say("   ", "en")
    assert not vm.busy and engine.controller.voice_requests == []
    (library.folder(root_id) / CHARACTER_FILE).unlink()
    vm = make_vm()
    vm.say("Hi", "en")
    assert not vm.busy and engine.controller.voice_requests == []


def test_play_phrase_downloads_then_plays(qtbot, engine, make_vm, root_id, player, tmp_path):
    engine.controller.voice_files[f"GET /v1/characters/{root_id}/phrases/P9/audio"] = b"RIFF nine"
    vm = make_vm()
    vm.play_phrase("P9")
    qtbot.waitUntil(lambda: bool(player.played), timeout=5000)
    assert player.played == [tmp_path / "cache" / f"{root_id}-P9.wav"]
    assert player.played[0].read_bytes() == b"RIFF nine"


def test_play_phrase_error_is_shown(qtbot, engine, make_vm, root_id, player):
    engine.controller.voice_answers[f"GET /v1/characters/{root_id}/phrases/P9/audio"] = (404, error_body("phrase_not_found"))
    vm = make_vm()
    vm.play_phrase("P9")
    qtbot.waitUntil(lambda: vm.error is not None, timeout=5000)
    assert vm.error.code == "phrase_not_found" and player.played == []


def test_delete_phrase_deletes_then_refreshes(qtbot, engine, make_vm, root_id):
    answers = engine.controller.voice_answers
    answers[f"DELETE /v1/characters/{root_id}/phrases/P1"] = (204, None)
    answers[f"GET /v1/characters/{root_id}/phrases"] = (200, [])
    vm = make_vm()
    with qtbot.waitSignal(vm.phrases_changed, timeout=5000):
        vm.delete_phrase("P1")
    assert requests(engine) == [("DELETE", f"/v1/characters/{root_id}/phrases/P1"),
                                ("GET", f"/v1/characters/{root_id}/phrases")]
    assert vm.phrases == []


def test_export_character_writes_the_package(qtbot, engine, make_api, tmp_path):
    engine.controller.voice_files["GET /v1/characters/C1/package"] = b"PK package"
    target = tmp_path / "pingu.m3dchar"
    done = []
    export_character(make_api(engine.url), "C1", target, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    assert done == [target]
    assert target.read_bytes() == b"PK package"


def test_import_character_posts_the_package(qtbot, engine, make_api, tmp_path):
    package = tmp_path / "pingu.m3dchar"
    package.write_bytes(b"PK data")
    engine.controller.voice_answers["POST /v1/characters/import"] = (201, {
        "character_id": "C7", "name": "Pingu", "created_at": TS, "input_file": "input.png",
        "versions": ["C7"], "voice_id": None, "phrase_count": 0})
    done = []
    import_character(make_api(engine.url), package, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    sent = engine.controller.voice_requests[-1]
    assert (sent["method"], sent["path"]) == ("POST", "/v1/characters/import")
    assert sent["files"]["package"]["data"] == b"PK data"
    assert done[0].character_id == "C7"
