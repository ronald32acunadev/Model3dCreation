import json
from datetime import UTC, datetime

import pytest
from model3d_contracts.characters import CHARACTER_FILE, Phrase
from model3d_contracts.jobs import JobError, JobOptions
from model3d_contracts.voices import Voice, VoiceKind

from model3d_client.services.library import Library
from model3d_client.viewmodels.characters import CharacterViewModel
from model3d_client.views.character_dialog import CharacterDialog


class NullPlayer:
    def play(self, path):
        pass


@pytest.fixture
def parts(qtbot, tmp_path, png, make_api):
    library = Library(tmp_path / "library")
    root = library.create(png, JobOptions())
    library.update(root.model_copy(update={"name": "Pingu"}))
    (library.folder(root.entry_id) / CHARACTER_FILE).write_text(json.dumps({"voice_id": "V1"}))
    vm = CharacterViewModel(make_api("http://127.0.0.1:9"), library, root.entry_id, NullPlayer(), tmp_path / "cache")
    vm.voices = [
        Voice(voice_id="V1", name="Narrator", kind=VoiceKind.designed, language="es",
              created_at=datetime(2026, 11, 1, tzinfo=UTC), transcript="Hola"),
        Voice(voice_id="V2", name="Hero", kind=VoiceKind.recorded, language="en",
              created_at=datetime(2026, 11, 2, tzinfo=UTC), transcript="Hi"),
    ]
    vm.phrases = [Phrase(phrase_id="P1", text="Hello there", language="en", created_at=datetime(2026, 11, 3, 9, 30, tzinfo=UTC))]
    dialog = CharacterDialog(vm)
    qtbot.addWidget(dialog)
    return vm, dialog


def test_renders_name_voices_and_phrases(parts):
    vm, dialog = parts
    assert dialog.heading.text() == "Pingu"
    combo = dialog.voice_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [None, "V1", "V2"]
    assert combo.currentData() == "V1"
    assert dialog.phrases.count() == 1
    text = dialog.phrases.item(0).text()
    assert text.startswith("Hello there\nen · ")


def test_say_is_disabled_without_text_or_voice(parts):
    vm, dialog = parts
    assert not dialog.say_button.isEnabled()
    dialog.text.setPlainText("Hola")
    assert dialog.say_button.isEnabled()
    vm.character = vm.character.model_copy(update={"voice_id": None})
    vm.changed.emit()
    assert not dialog.say_button.isEnabled()


def test_busy_disables_say_and_shows_progress(parts):
    vm, dialog = parts
    dialog.text.setPlainText("Hola")
    vm.busy, vm.progress, vm.message = True, 0.5, "Speaking"
    vm.changed.emit()
    assert not dialog.say_button.isEnabled()
    assert dialog.progress.value() == 50
    assert dialog.progress_message.text() == "Speaking"


def test_error_is_shown(parts):
    vm, dialog = parts
    vm.error = JobError(code="voice_not_assigned", message="Pick a voice for this character first")
    vm.changed.emit()
    assert dialog.error_label.text() == "Pick a voice for this character first"


def test_play_and_delete_need_a_selected_phrase(parts):
    _, dialog = parts
    assert not dialog.play_button.isEnabled() and not dialog.delete_button.isEnabled()
    dialog.phrases.setCurrentRow(0)
    assert dialog.play_button.isEnabled() and dialog.delete_button.isEnabled()
