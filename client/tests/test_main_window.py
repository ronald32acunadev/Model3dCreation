import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobOptions, Quality
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from fakes import FakeSupervisor, StubViewer
from model3d_client.paths import AppPaths
from model3d_client.services.animator import RigAnimator
from model3d_client.services.converter import ModelConverter
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


def test_quality_combo_offers_high_detail(window):
    combo = window.generation_panel.quality_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [Quality.draft, Quality.final, Quality.high]
    assert combo.itemText(2) == "Quality: high detail"


def ready_window(qtbot, tmp_path, make_api, supervisor):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    win = MainWindow(MainViewModel(paths, api, supervisor, library, generation, Settings()), viewer=StubViewer())
    win.ask_close_anyway = lambda: True
    qtbot.addWidget(win)
    return win


def test_rig_checkbox_disabled_with_reason(qtbot, tmp_path, make_api):
    reason = "UniRig weights are missing from data/hf (see docs/setup.md)"
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor(rig=False, rig_reason=reason))
    check = win.generation_panel.rig_check
    assert not check.isEnabled()
    assert reason in check.toolTip()


def test_rig_checkbox_sends_rig_option(qtbot, tmp_path, make_api):
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor())
    check = win.generation_panel.rig_check
    assert check.isEnabled() and check.text() == "With skeleton"
    check.setChecked(True)
    assert win.vm.generation.rig is True


def test_rigging_phase_has_its_text(qtbot, tmp_path, make_api):
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor())
    win.vm.generation._set_phase(Phase.rigging, 0.4, "Skeleton ready")
    assert win.generation_panel.stage_label.text() == "Adding skeleton… — Skeleton ready"


def test_library_menu_add_skeleton_starts_rig_job(qtbot, tmp_path, make_api, png):
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor())
    plain = add_succeeded(win.vm.library, png, "Robot")
    win.vm.entries_changed.emit()
    menu = win.library_panel.build_menu(plain)
    assert next(a for a in menu.actions() if a.objectName() == "add_skeleton").isEnabled()
    started = []
    win.vm.generation.changed.connect(lambda: started.append(win.vm.generation.phase))
    win.library_panel.add_skeleton_requested.emit(plain.entry_id)
    assert Phase.submitting in started
    rigged = [entry for entry in win.vm.library.entries() if entry.source_entry_id == plain.entry_id]
    assert len(rigged) == 1 and rigged[0].name == "Robot (rigged)"


def test_add_skeleton_disabled_for_rigged_entry(qtbot, tmp_path, make_api, png):
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor())
    entry = add_succeeded(win.vm.library, png, "Robot")
    rigged = entry.model_copy(update={"options": JobOptions(rig=True)})
    win.vm.library.update(rigged)
    win.vm.entries_changed.emit()
    menu = win.library_panel.build_menu(rigged)
    assert not next(a for a in menu.actions() if a.objectName() == "add_skeleton").isEnabled()
    assert "skeleton" in win.library_panel.list.item(0).text()


def test_library_rescans_when_app_becomes_active(qtbot, window, png, qapp):
    add_succeeded(Library(window.vm.library.root), png, "From Blender")
    assert window.library_panel.list.count() == 0
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert window.library_panel.list.count() == 1


def test_half_written_entry_is_skipped(qtbot, window, png, qapp):
    add_succeeded(window.vm.library, png, "Robot")
    broken = window.vm.library.root / "20261022T120000-abcdef"
    broken.mkdir()
    (broken / "entry.json").write_text('{"entry_id": "20261022T1200')
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert window.library_panel.list.count() == 1


def test_rig_reason_is_visible_text_and_menu_tooltip(qtbot, tmp_path, make_api, png):
    reason = "UniRig weights are missing from data/hf (see docs/setup.md)"
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor(rig=False, rig_reason=reason))
    hint = win.generation_panel.rig_hint
    assert not hint.isHidden() and reason in hint.text()
    entry = add_succeeded(win.vm.library, png, "Robot")
    win.vm.entries_changed.emit()
    menu = win.library_panel.build_menu(entry)
    action = next(a for a in menu.actions() if a.objectName() == "add_skeleton")
    assert not action.isEnabled() and reason in action.toolTip()
    assert menu.toolTipsVisible()


def test_rig_hint_hidden_when_rig_available(qtbot, tmp_path, make_api):
    win = ready_window(qtbot, tmp_path, make_api, FakeSupervisor())
    assert win.generation_panel.rig_hint.isHidden()


def test_rig_hint_hidden_before_the_engine_answers(window):
    window.vm.supervisor.health = None
    window.vm.generation.changed.emit()
    assert window.generation_panel.rig_hint.isHidden()


def make_window(qtbot, tmp_path, make_api, body: str) -> MainWindow:
    """A window whose export runs a fake engine Python that executes `body` as a shell script."""
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n" + body)
    python.chmod(0o755)
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(state=EngineState.loading)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    checks = lambda _paths: []
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=checks,
                       converter=ModelConverter(python))
    win = MainWindow(vm, viewer=StubViewer())
    win.ask_close_anyway = lambda: True
    qtbot.addWidget(win)
    return win


def add_rigged(library, png, name="robot"):
    entry = library.create(png, JobOptions(rig=True))
    library.model_path(entry.entry_id).write_bytes(b"glTF")
    stats = ResultStats(vertices=1, faces=2, textured=True, duration_s=1.0)
    entry = entry.model_copy(update={"outcome": Outcome.succeeded, "stats": stats, "name": name})
    library.update(entry)
    return entry


def test_export_fbx_writes_file_and_reports(qtbot, tmp_path, make_api, png):
    win = make_window(qtbot, tmp_path, make_api, 'printf data > "$3"\nexit 0\n')
    entry = add_succeeded(win.vm.library, png)
    win.vm.select(entry.entry_id)
    win.ask_export_path = lambda start: (str(tmp_path / "robot"), "FBX (*.fbx)")
    win.generation_panel.save_button.click()
    assert not win.generation_panel.save_button.isEnabled()
    assert "Exporting" in win.statusBar().currentMessage()
    with qtbot.waitSignal(win.vm.export_finished, timeout=5000):
        pass
    assert (tmp_path / "robot.fbx").read_bytes() == b"data"
    assert "Exported" in win.statusBar().currentMessage()
    assert win.generation_panel.save_button.isEnabled()


def test_failed_export_shows_warning(qtbot, tmp_path, make_api, png, monkeypatch):
    win = make_window(qtbot, tmp_path, make_api, "echo nope >&2\nexit 1\n")
    entry = add_succeeded(win.vm.library, png)
    win.vm.select(entry.entry_id)
    win.ask_export_path = lambda start: (str(tmp_path / "robot"), "STL (*.stl)")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    win.warn_format_drops = lambda found: None  # STL drops the texture: no modal dialog in tests
    with qtbot.waitSignal(win.vm.export_failed, timeout=5000):
        win.generation_panel.save_button.click()
    assert len(warnings) == 1
    assert "Could not export the model" in warnings[0][2] and "nope" in warnings[0][2]
    assert win.generation_panel.save_button.isEnabled()


def test_obj_export_of_rigged_model_warns_about_skeleton(qtbot, tmp_path, make_api, png):
    win = make_window(qtbot, tmp_path, make_api, 'printf data > "$3"\nexit 0\n')
    entry = add_rigged(win.vm.library, png)
    win.vm.select(entry.entry_id)
    win.ask_export_path = lambda start: (str(tmp_path / "robot"), "Wavefront OBJ (*.obj)")
    drops = []
    win.warn_format_drops = lambda found: drops.append(set(found))
    with qtbot.waitSignal(win.vm.export_finished, timeout=5000):
        win.generation_panel.save_button.click()
    assert drops == [{"skeleton"}]
    assert (tmp_path / "robot.obj").exists()


DETECT_BIPED = 'echo \'MODEL3D_RESULT {"body": "biped", "legs": [], "arms": [], "up": 1}\'\nexit 0\n'
DETECT_FAIL = "echo broken >&2\nexit 1\n"


def animating_window(qtbot, tmp_path, make_api, body: str) -> MainWindow:
    """A window whose body detection runs a fake engine Python that executes `body` as a shell script."""
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n" + body)
    python.chmod(0o755)
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(state=EngineState.loading)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=lambda _paths: [],
                       animator=RigAnimator(python))
    win = MainWindow(vm, viewer=StubViewer())
    win.ask_close_anyway = lambda: True
    qtbot.addWidget(win)
    return win


def test_add_walk_asks_with_the_detected_body(qtbot, tmp_path, make_api, png):
    win = animating_window(qtbot, tmp_path, make_api, DETECT_BIPED)
    entry = add_rigged(win.vm.library, png)
    asked = []
    win.ask_body_type = lambda detected: asked.append(detected)  # cancel: no walk
    with qtbot.waitSignal(win.vm.body_detected, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert asked == ["biped"]


def test_chosen_body_starts_the_walk(qtbot, tmp_path, make_api, png):
    win = animating_window(qtbot, tmp_path, make_api, DETECT_BIPED)
    entry = add_rigged(win.vm.library, png)
    win.ask_body_type = lambda detected: "quadruped"
    walks = []
    win.vm.add_walk = lambda entry_id, body: walks.append((entry_id, body))
    with qtbot.waitSignal(win.vm.body_detected, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert walks == [(entry.entry_id, "quadruped")]


def test_cancelled_dialog_starts_no_walk(qtbot, tmp_path, make_api, png):
    win = animating_window(qtbot, tmp_path, make_api, DETECT_BIPED)
    entry = add_rigged(win.vm.library, png)
    win.ask_body_type = lambda detected: None
    walks = []
    win.vm.add_walk = lambda entry_id, body: walks.append((entry_id, body))
    with qtbot.waitSignal(win.vm.body_detected, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert walks == []


def test_failed_detection_shows_warning(qtbot, tmp_path, make_api, png, monkeypatch):
    win = animating_window(qtbot, tmp_path, make_api, DETECT_FAIL)
    entry = add_rigged(win.vm.library, png)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    with qtbot.waitSignal(win.vm.walk_failed, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert len(warnings) == 1
    assert "Could not add the walk animation" in warnings[0][2] and "broken" in warnings[0][2]
    assert win.vm.library.entries() and len(win.vm.library.entries()) == 1
