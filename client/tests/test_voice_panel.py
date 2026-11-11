import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

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


def test_panel_renders_the_voices(qtbot, engine, vm, panel):
    load_voices(qtbot, engine, vm)
    assert panel.voice_list.count() == 2
    assert panel.voice_list.item(0).text().startswith("Narrator")
    assert [panel.generate_voice_combo.itemText(i) for i in range(2)] == ["Narrator", "Hero"]


def test_voice_buttons_need_a_selected_voice(qtbot, engine, vm, panel):
    load_voices(qtbot, engine, vm)
    assert not panel.play_voice_button.isEnabled()
    panel.voice_list.setCurrentRow(1)
    assert panel.play_voice_button.isEnabled() and panel.delete_voice_button.isEnabled()
    assert panel.selected_voice_id() == "V2"


def test_unavailable_voice_disables_controls_and_shows_the_reason(qtbot, make_api, engine, tmp_path):
    supervisor = FakeSupervisor(voice=False, voice_reason="Voice model not installed")
    voice_vm = VoiceViewModel(make_api(engine.url), supervisor, tmp_path / "cache", FakePlayer(), FakeRecorder)
    widget = VoicePanel(voice_vm)
    qtbot.addWidget(widget)
    widget.show()
    assert widget.banner.isVisible() and widget.banner.text() == "Voice model not installed"
    assert not widget.tabs.isEnabled() and not widget.voice_list.isEnabled()
    supervisor.set_voice(True)
    assert not widget.banner.isVisible() and widget.tabs.isEnabled()


def test_engine_not_ready_banner(qtbot, make_api, engine, tmp_path):
    supervisor = FakeSupervisor(state=EngineState.loading, voice=True)
    widget = VoicePanel(VoiceViewModel(make_api(engine.url), supervisor, tmp_path / "cache", FakePlayer(), FakeRecorder))
    qtbot.addWidget(widget)
    assert widget.banner.text() == "The engine is not ready yet."


def test_preview_button_needs_description_and_sample_text(panel):
    assert not panel.preview_button.isEnabled()
    panel.description_edit.setPlainText("a deep voice")
    assert not panel.preview_button.isEnabled()
    panel.sample_edit.setPlainText("Hello")
    assert panel.preview_button.isEnabled()
    panel.description_edit.setPlainText("   ")
    assert not panel.preview_button.isEnabled()


def test_generate_button_needs_voice_and_text(qtbot, engine, vm, panel):
    assert not panel.generate_button.isEnabled()
    load_voices(qtbot, engine, vm)
    assert not panel.generate_button.isEnabled()
    panel.generate_text_edit.setPlainText("Hola")
    assert panel.generate_button.isEnabled()


def test_save_voice_needs_a_name_and_a_preview(panel, vm):
    panel.preview_name_edit.setText("Deep")
    assert not panel.save_preview_button.isEnabled()
    vm.preview_id = "PV1"
    vm.changed.emit()
    assert panel.save_preview_button.isEnabled()
    panel.preview_name_edit.setText("")
    assert not panel.save_preview_button.isEnabled()


def test_record_button_toggles_and_take_buttons_follow(qtbot, panel, vm):
    assert panel.record_button.text() == "Record" and not panel.play_take_button.isEnabled()
    qtbot.mouseClick(panel.record_button, Qt.MouseButton.LeftButton)
    assert panel.record_button.text() == "Stop"
    vm._recorder.level.emit(0.5)
    assert panel.level_bar.value() == 50
    qtbot.mouseClick(panel.record_button, Qt.MouseButton.LeftButton)
    assert panel.record_button.text() == "Record"
    assert panel.play_take_button.isEnabled() and panel.rerecord_button.isEnabled()
    assert not panel.save_recording_button.isEnabled()
    panel.record_name_edit.setText("Mine")
    assert panel.save_recording_button.isEnabled()


def test_error_is_shown(panel, vm):
    from model3d_contracts.jobs import JobError

    vm._set_error(JobError(code="invalid_name", message="x"))
    assert panel.error_label.text() == "Give the voice a name."
    vm._set_error(JobError(code="unknown_code", message="Engine says no"))
    assert panel.error_label.text() == "Engine says no"


def test_delete_asks_before_calling_the_engine(qtbot, engine, vm, panel):
    load_voices(qtbot, engine, vm)
    engine.controller.voice_answers["DELETE /v1/voices/V1"] = (204, None)
    panel.voice_list.setCurrentRow(0)
    panel.confirm_delete = lambda name: False
    panel.delete_voice_button.click()
    assert not [r for r in engine.controller.voice_requests if r["method"] == "DELETE"]
    panel.confirm_delete = lambda name: True
    panel.delete_voice_button.click()
    qtbot.waitUntil(lambda: any(r["method"] == "DELETE" for r in engine.controller.voice_requests), timeout=5000)


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
