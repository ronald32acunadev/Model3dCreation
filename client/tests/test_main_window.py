import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobOptions, Quality
from PySide6.QtCore import Qt

from fakes import FakeSupervisor, StubViewer
from model3d_client.paths import AppPaths
from model3d_client.services.diagnostics import Check, CheckId
from model3d_client.services.library import Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState
from model3d_client.viewmodels.generation import GenerationViewModel, Phase
from model3d_client.viewmodels.main import MainViewModel
from model3d_client.views.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, make_api):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(state=EngineState.loading)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    checks = lambda _paths: [Check(CheckId.weights, False, str(paths.hf))]
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=checks)
    win = MainWindow(vm, viewer=StubViewer())
    win.ask_close_anyway = lambda: True  # no modal dialog when qtbot closes the window at teardown
    qtbot.addWidget(win)
    return win


def add_succeeded(library, png, name="penguin"):
    entry = library.create(png, JobOptions())
    library.model_path(entry.entry_id).write_bytes(b"glTF")
    stats = ResultStats(vertices=1, faces=2, textured=True, duration_s=135.0)
    entry = entry.model_copy(update={"outcome": Outcome.succeeded, "stats": stats, "name": name})
    library.update(entry)
    return entry


def test_generate_button_follows_can_generate(window, png):
    panel, generation, supervisor = window.generation_panel, window.vm.generation, window.vm.supervisor
    assert not panel.generate_button.isEnabled()
    generation.set_image(png)
    assert not panel.generate_button.isEnabled()  # engine still loading
    supervisor.set_state(EngineState.ready)
    assert panel.generate_button.isEnabled()
    assert panel.generate_button.text() == "Generate"


def test_running_job_turns_button_into_cancel(window, png):
    panel, generation = window.generation_panel, window.vm.generation
    window.vm.supervisor.set_state(EngineState.ready)
    generation.set_image(png)
    generation._set_phase(Phase.painting, 0.4, "Painting views")
    assert panel.generate_button.text() == "Cancel" and panel.generate_button.isEnabled()
    assert panel.progress_bar.value() == 40
    assert "Painting" in panel.stage_label.text()
    generation._set_phase(Phase.cancelling, 0.4, "")
    assert panel.generate_button.text() == "Cancelling…" and not panel.generate_button.isEnabled()


def test_image_problem_is_shown(window, tmp_path):
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a")
    window.vm.generation.set_image(gif)
    assert window.generation_panel.error_label.text() == "Use a PNG or JPEG image."


def test_error_codes_map_to_sentences(window):
    window.vm.generation.error = JobError(code="gpu_out_of_memory", message="raw")
    window.vm.generation.changed.emit()
    assert "ran out of GPU memory" in window.generation_panel.error_label.text()
    window.vm.generation.error = JobError(code="something_new", message="Engine says hi")
    window.vm.generation.changed.emit()
    assert window.generation_panel.error_label.text() == "Engine says hi"


def test_texture_disabled_with_reason_in_shape_only_mode(qtbot, tmp_path, make_api):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(texture=False)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    win = MainWindow(MainViewModel(paths, api, supervisor, library, generation, Settings()), viewer=StubViewer())
    qtbot.addWidget(win)
    assert not win.generation_panel.texture_check.isEnabled()
    assert win.generation_panel.texture_check.toolTip()


def test_quality_combo_sets_view_model(window):
    combo = window.generation_panel.quality_combo
    combo.setCurrentIndex(combo.findData(Quality.draft))
    assert window.vm.generation.quality == Quality.draft


def test_library_lists_entries_and_click_selects(window, png):
    entry = add_succeeded(window.vm.library, png, "Robot")
    window.vm.entries_changed.emit()
    items = window.library_panel.list
    assert items.count() == 1
    assert "Robot" in items.item(0).text()
    items.setCurrentRow(0)
    assert window.vm.selected.entry_id == entry.entry_id


def test_selecting_entry_shows_model(window, png):
    entry = add_succeeded(window.vm.library, png)
    window.vm.select(entry.entry_id)
    assert window.viewer.shown == window.vm.library.model_path(entry.entry_id)
    assert window.generation_panel.save_button.isEnabled()
    window.vm.select(None)
    assert window.viewer.shown is None
    assert not window.generation_panel.save_button.isEnabled()


def test_texture_toggle_and_reset_reach_viewer(window):
    panel = window.generation_panel
    panel.show_texture_check.setChecked(False)
    assert window.viewer.textured is False
    panel.reset_camera_button.click()
    assert window.viewer.resets == 1


def test_diagnostics_panel_shown_on_crash(qtbot, window):
    window.vm.supervisor.failure = "The engine exited with code 1"
    with qtbot.waitSignal(window.vm.diagnostics_changed, timeout=5000):
        window.vm.supervisor.set_state(EngineState.crashed)
    assert window.center.currentWidget() is window.diagnostics_panel
    text = window.diagnostics_panel.text()
    assert "model weights" in text and "exited with code 1" in text
    window.diagnostics_panel.restart_button.click()
    assert window.vm.supervisor.restarted == 1


def test_top_bar_shows_state_and_mode(window):
    window.vm.supervisor.set_state(EngineState.ready)
    window.vm.supervisor.health_changed.emit(window.vm.supervisor.health)
    text = window.top_bar.state_label.text()
    assert "ready" in text.lower()
    assert "dual_gpu" in window.top_bar.mode_label.text()


def test_close_while_generating_can_be_refused(window, monkeypatch):
    window.vm.generation.phase = Phase.shaping
    monkeypatch.setattr(window, "ask_close_anyway", lambda: False)
    window.show()
    window.close()
    assert window.isVisible()
    assert window.vm.supervisor.stopped == 0


def test_close_stops_engine(qtbot, window):
    window.show()
    window.close()
    assert window.vm.supervisor.stopped == 1
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=2000)


def test_text_tab_is_disabled(window):
    tabs = window.generation_panel.source_tabs
    assert tabs.count() == 2
    assert not tabs.isTabEnabled(1)


def test_drop_area_accepts_image_path(window, png):
    window.generation_panel.image_dropped(png)
    assert window.vm.generation.image == png
    assert not window.generation_panel.image_preview.pixmap().isNull()
