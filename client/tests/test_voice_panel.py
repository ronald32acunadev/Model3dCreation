import pytest
from PySide6.QtWidgets import QLabel, QTabWidget, QWidget

from fake_engine import FakeEngine
from fakes import FakePlayer, FakeRecorder, FakeSupervisor, StubViewer
from model3d_client.paths import AppPaths
from model3d_client.services.library import Library
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState
from model3d_client.viewmodels.generation import GenerationViewModel
from model3d_client.viewmodels.main import MainViewModel
from model3d_client.viewmodels.voice import VoiceViewModel
from model3d_client.views.main_window import MainWindow
from model3d_client.views.new_voice_dialog import NewVoiceDialog
from model3d_client.views.voice_panel import VoicePanel

TS = "2026-11-01T12:00:00Z"


def voice_json(voice_id, name):
    return {"voice_id": voice_id, "name": name, "kind": "designed", "language": "es",
            "created_at": TS, "transcript": "Hola"}


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


@pytest.fixture
def supervisor():
    return FakeSupervisor(voice=True)


@pytest.fixture
def vm(make_api, engine, supervisor, tmp_path):
    return VoiceViewModel(make_api(engine.url), supervisor, tmp_path / "cache", FakePlayer(), FakeRecorder)


@pytest.fixture
def panel(qtbot, vm):
    widget = VoicePanel(vm)
    qtbot.addWidget(widget)
    return widget


def load_voices(qtbot, engine, vm):
    engine.controller.voice_answers["GET /v1/voices"] = (200, [voice_json("V1", "Narrator"), voice_json("V2", "Hero")])
    vm.refresh()
    qtbot.waitUntil(lambda: len(vm.voices) == 2, timeout=5000)


def test_panel_has_no_tabs_and_lists_voices_with_play(qtbot, engine, vm, panel):
    assert not panel.findChildren(QTabWidget)
    load_voices(qtbot, engine, vm)
    assert panel.voice_list.count() == 2
    assert sorted(panel.row_play_buttons) == ["V1", "V2"]
    assert [panel.generate_voice_combo.itemText(i) for i in range(2)] == ["Narrator", "Hero"]
    assert panel.row_play_buttons["V1"].isEnabled()


def test_row_play_button_plays_that_voice_sample(qtbot, engine, vm, panel):
    engine.controller.voice_files["GET /v1/voices/V2/sample"] = b"RIFF hero"
    load_voices(qtbot, engine, vm)
    panel.row_play_buttons["V2"].click()
    qtbot.waitUntil(lambda: bool(vm.player.played), timeout=5000)
    assert vm.player.played[0].read_bytes() == b"RIFF hero"


def test_empty_hint_shows_only_without_voices(qtbot, engine, vm, panel):
    panel.show()
    assert panel.empty_hint.isVisible() and "New voice" in panel.empty_hint.text()
    load_voices(qtbot, engine, vm)
    assert not panel.empty_hint.isVisible()


def test_new_voice_button_opens_the_dialog(qtbot, panel, monkeypatch):
    opened = []
    monkeypatch.setattr(NewVoiceDialog, "open", lambda self: opened.append(self))
    panel.new_voice_button.click()
    assert len(opened) == 1 and isinstance(opened[0], NewVoiceDialog)
    assert panel.new_voice_dialog is opened[0]
    opened[0].reject()
    assert panel.new_voice_dialog is None


def test_unavailable_voice_shows_the_reason_once_and_disables_buttons(qtbot, make_api, engine, tmp_path):
    supervisor = FakeSupervisor(voice=False, voice_reason="Voice model not installed")
    voice_vm = VoiceViewModel(make_api(engine.url), supervisor, tmp_path / "cache", FakePlayer(), FakeRecorder)
    widget = VoicePanel(voice_vm)
    qtbot.addWidget(widget)
    widget.show()
    assert widget.banner.isVisible() and widget.banner.text() == "Voice model not installed"
    assert not widget.new_voice_button.isEnabled() and not widget.generate_button.isEnabled()
    assert not widget.voice_list.isEnabled()
    shown = [label for label in widget.findChildren(QLabel) if label.isVisible() and label.text() == "Voice model not installed"]
    assert len(shown) == 1
    supervisor.set_voice(True)
    assert not widget.banner.isVisible() and widget.new_voice_button.isEnabled()


def test_engine_not_ready_banner(qtbot, make_api, engine, tmp_path):
    supervisor = FakeSupervisor(state=EngineState.loading, voice=True)
    widget = VoicePanel(VoiceViewModel(make_api(engine.url), supervisor, tmp_path / "cache", FakePlayer(), FakeRecorder))
    qtbot.addWidget(widget)
    assert widget.banner.text() == "The engine is not ready yet."


def test_speak_button_needs_voice_and_text(qtbot, engine, vm, panel):
    assert not panel.generate_button.isEnabled()
    load_voices(qtbot, engine, vm)
    assert not panel.generate_button.isEnabled()
    panel.generate_text_edit.setPlainText("Hola")
    assert panel.generate_button.isEnabled()
    panel.generate_text_edit.setPlainText("  ")
    assert not panel.generate_button.isEnabled()


def test_error_is_shown(panel, vm):
    from model3d_contracts.jobs import JobError

    vm._set_error(JobError(code="invalid_name", message="x"))
    assert panel.error_label.text() == "Give the voice a name."
    vm._set_error(JobError(code="unknown_code", message="Engine says no"))
    assert panel.error_label.text() == "Engine says no"


def test_delete_asks_before_calling_the_engine(qtbot, engine, vm, panel):
    load_voices(qtbot, engine, vm)
    engine.controller.voice_answers["DELETE /v1/voices/V1"] = (204, None)
    panel.confirm_delete = lambda name: False
    panel.delete_voice("V1")
    assert not [r for r in engine.controller.voice_requests if r["method"] == "DELETE"]
    panel.confirm_delete = lambda name: True
    panel.delete_voice("V1")
    qtbot.waitUntil(lambda: any(r["method"] == "DELETE" for r in engine.controller.voice_requests), timeout=5000)


def test_rename_asks_for_a_name_then_patches(qtbot, engine, vm, panel):
    load_voices(qtbot, engine, vm)
    engine.controller.voice_answers["PATCH /v1/voices/V1"] = (200, voice_json("V1", "Bard"))
    panel.ask_name = lambda title, label, current: None
    panel.rename_voice("V1")
    assert not [r for r in engine.controller.voice_requests if r["method"] == "PATCH"]
    panel.ask_name = lambda title, label, current: "Bard"
    panel.rename_voice("V1")
    qtbot.waitUntil(lambda: any(r["method"] == "PATCH" for r in engine.controller.voice_requests), timeout=5000)


@pytest.fixture
def window_parts(tmp_path, make_api):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(state=EngineState.loading)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    return MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=lambda _paths: [])


def test_section_switch_swaps_the_pages(qtbot, window_parts):
    page = QWidget()
    window = MainWindow(window_parts, StubViewer(), voice_panel=page)
    window.ask_close_anyway = lambda: True
    qtbot.addWidget(window)
    assert not window.top_bar.voice_button.isHidden() and not window.top_bar.models_button.isHidden()
    assert window.sections.currentIndex() == 0 and window.top_bar.models_button.isChecked()
    window.top_bar.voice_button.click()
    assert window.sections.currentWidget() is page and window.top_bar.voice_button.isChecked()
    assert not window.top_bar.models_button.isChecked()
    window.top_bar.models_button.click()
    assert window.sections.currentIndex() == 0


def test_no_voice_panel_means_no_switch(qtbot, window_parts):
    window = MainWindow(window_parts, StubViewer())
    window.ask_close_anyway = lambda: True
    qtbot.addWidget(window)
    assert window.top_bar.voice_button.isHidden() and window.top_bar.models_button.isHidden()
    assert window.sections.count() == 1
