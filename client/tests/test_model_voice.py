import json

import pytest
from model3d_contracts.characters import CHARACTER_FILE
from model3d_contracts.jobs import JobOptions
from model3d_contracts.voices import Voice
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from fake_engine import CLOSE, FakeEngine, make_job
from model3d_client.services.library import Library
from model3d_client.viewmodels.characters import CharacterViewModel
from model3d_client.views.model_voice_panel import NEW_VOICE, ModelVoicePanel

TS = "2026-11-01T12:00:00Z"


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, path):
        self.played.append(path)


class FakeDialog(QDialog):
    voice_saved = Signal(object)
    opened = []

    def __init__(self, parent=None):
        super().__init__(parent)
        FakeDialog.opened.append(self)

    def open(self):  # never show a window in tests
        pass


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


@pytest.fixture
def library(tmp_path, png):
    return Library(tmp_path / "library")


@pytest.fixture
def root_id(library, png):
    return library.create(png, JobOptions()).entry_id


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def panel(qtbot, engine, make_api, library, player, tmp_path):
    FakeDialog.opened = []
    vm = CharacterViewModel(make_api(engine.url), library, None, player, tmp_path / "cache")
    widget = ModelVoicePanel(vm, lambda parent: FakeDialog(parent))
    qtbot.addWidget(widget)
    return widget


def voice_json(voice_id="V1", name="Narrator"):
    return {"voice_id": voice_id, "name": name, "kind": "designed", "language": "es", "created_at": TS,
            "transcript": "Hola"}


def phrase_json(phrase_id="S1", text="Hello there"):
    return {"phrase_id": phrase_id, "text": text, "language": "en", "created_at": TS}


def link(library, root_id, voice_id="V1"):
    (library.folder(root_id) / CHARACTER_FILE).write_text(json.dumps({"voice_id": voice_id}))


def open_character(qtbot, engine, panel, root_id, voices=None, phrases=None):
    answers = engine.controller.voice_answers
    answers["GET /v1/voices"] = (200, voices if voices is not None else [voice_json("V1"), voice_json("V2", "Hero")])
    answers[f"GET /v1/characters/{root_id}/phrases"] = (200, phrases or [])
    panel.vm.set_character(root_id)
    qtbot.waitUntil(lambda: len(panel.vm.voices) > 0, timeout=5000)


def test_no_selection_shows_the_hint(panel):
    assert not panel.empty_label.isHidden()
    assert panel.content.isHidden()
    assert panel.empty_label.text() == "Select a model to give it a voice"


def test_selected_character_shows_name_and_no_voice(qtbot, engine, panel, root_id):
    open_character(qtbot, engine, panel, root_id)
    assert panel.empty_label.isHidden() and not panel.content.isHidden()
    assert panel.vm.name in panel.title_label.text()
    assert panel.voice_label.text() == "No voice"
    assert [panel.voice_combo.itemText(i) for i in range(panel.voice_combo.count())][1:] == [
        "Narrator", "Hero", "New voice…"]


def test_linking_through_the_combo_puts_the_voice(qtbot, engine, panel, root_id):
    open_character(qtbot, engine, panel, root_id)
    engine.controller.voice_answers[f"PUT /v1/characters/{root_id}/voice"] = (200, {})
    panel.voice_combo.setCurrentIndex(panel.voice_combo.findData("V2"))
    with qtbot.waitSignal(panel.vm.changed, timeout=5000):
        panel._on_voice_picked(panel.voice_combo.currentIndex())
    sent = engine.controller.voice_requests[-1]
    assert (sent["method"], sent["path"], sent["json"]) == ("PUT", f"/v1/characters/{root_id}/voice", {"voice_id": "V2"})
    assert panel.voice_label.text() == "Voice: Hero"


def test_new_voice_opens_the_dialog_and_links_the_saved_voice(qtbot, engine, panel, root_id):
    open_character(qtbot, engine, panel, root_id)
    engine.controller.voice_answers[f"PUT /v1/characters/{root_id}/voice"] = (200, {})
    panel.voice_combo.setCurrentIndex(panel.voice_combo.findData(NEW_VOICE))
    panel._on_voice_picked(panel.voice_combo.currentIndex())
    assert len(FakeDialog.opened) == 1
    assert panel.voice_combo.currentData() is None  # back on the placeholder until a voice is saved
    saved = Voice.model_validate(voice_json("V3", "Fresh"))
    engine.controller.voice_answers["GET /v1/voices"] = (200, [voice_json("V1"), voice_json("V3", "Fresh")])
    with qtbot.waitSignal(panel.vm.changed, timeout=5000):
        FakeDialog.opened[0].voice_saved.emit(saved)
    qtbot.waitUntil(lambda: panel.voice_label.text() == "Voice: Fresh", timeout=5000)
    puts = [r for r in engine.controller.voice_requests if r["method"] == "PUT"]
    assert puts[-1]["json"] == {"voice_id": "V3"}


def test_unlink_sends_delete(qtbot, engine, panel, library, root_id):
    link(library, root_id)
    open_character(qtbot, engine, panel, root_id)
    assert panel.voice_label.text() == "Voice: Narrator" and panel.unlink_button.isEnabled()
    engine.controller.voice_answers[f"DELETE /v1/characters/{root_id}/voice"] = (204, None)
    with qtbot.waitSignal(panel.vm.changed, timeout=5000):
        panel.unlink_button.click()
    assert [(r["method"], r["path"]) for r in engine.controller.voice_requests][-1] == (
        "DELETE", f"/v1/characters/{root_id}/voice")
    assert panel.voice_label.text() == "No voice"
    assert not panel.unlink_button.isEnabled()


def test_say_is_disabled_without_a_voice_and_without_text(qtbot, engine, panel, library, root_id):
    open_character(qtbot, engine, panel, root_id)
    panel.text.setPlainText("Hi")
    assert not panel.say_button.isEnabled()
    assert panel.say_hint.text() == "Link a voice first"
    assert panel.say_button.toolTip() == "Link a voice first"
    panel.vm.say("Hi", "en")  # the view model refuses too
    assert panel.vm.say_hint == "Link a voice first" and not panel.vm.busy
    assert not [r for r in engine.controller.voice_requests if r["method"] == "POST"]
    link(library, root_id)
    panel.vm.set_character(None)
    open_character(qtbot, engine, panel, root_id)
    panel.text.setPlainText("   ")
    assert not panel.say_button.isEnabled() and panel.say_hint.text() == ""
    panel.text.setPlainText("Hello there")
    assert panel.say_button.isEnabled()


def test_say_posts_plays_and_lists_the_phrase(qtbot, engine, panel, library, root_id, player, tmp_path):
    link(library, root_id)
    open_character(qtbot, engine, panel, root_id)
    answers = engine.controller.voice_answers
    answers[f"POST /v1/characters/{root_id}/speech"] = (202, make_job("S1"))
    answers[f"GET /v1/characters/{root_id}/phrases"] = (200, [phrase_json("S1", "Hello there")])
    engine.controller.voice_files[f"GET /v1/characters/{root_id}/phrases/S1/audio"] = b"RIFF speech"
    engine.controller.push(
        {"type": "completed", "job_id": "S1", "ts": TS, "result_url": "/v1/speech-jobs/S1/result"}, CLOSE)
    panel.text.setPlainText("Hello there")
    panel.language_combo.setCurrentIndex(panel.language_combo.findData("en"))
    panel.say_button.click()
    qtbot.waitUntil(lambda: bool(player.played), timeout=10000)
    posted = next(r for r in engine.controller.voice_requests if r["method"] == "POST")
    assert posted["json"] == {"text": "Hello there", "language": "en"}
    assert player.played == [tmp_path / "cache" / f"{root_id}-S1.wav"]
    assert panel.phrases.count() == 1 and "Hello there" in panel.phrases.item(0).text()


def test_confirmed_phrase_delete_sends_delete(qtbot, engine, panel, library, root_id, monkeypatch):
    link(library, root_id)
    open_character(qtbot, engine, panel, root_id, phrases=[phrase_json("P1")])
    qtbot.waitUntil(lambda: panel.phrases.count() == 1, timeout=5000)
    panel.phrases.setCurrentRow(0)
    answers = engine.controller.voice_answers
    answers[f"DELETE /v1/characters/{root_id}/phrases/P1"] = (204, None)
    asked = []

    def decline(*args):
        asked.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    panel.delete_button.click()
    assert asked and not [r for r in engine.controller.voice_requests if r["method"] == "DELETE"]
    answers[f"GET /v1/characters/{root_id}/phrases"] = (200, [])
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(panel.vm.phrases_changed, timeout=5000):
        panel.delete_button.click()
    assert ("DELETE", f"/v1/characters/{root_id}/phrases/P1") in [
        (r["method"], r["path"]) for r in engine.controller.voice_requests]
    assert panel.phrases.count() == 0


def test_switching_characters_clears_the_phrases(qtbot, engine, panel, root_id):
    open_character(qtbot, engine, panel, root_id, phrases=[phrase_json("P1")])
    qtbot.waitUntil(lambda: panel.phrases.count() == 1, timeout=5000)
    panel.vm.set_character(None)
    assert panel.phrases.count() == 0 and not panel.empty_label.isHidden()
