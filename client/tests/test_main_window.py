from pathlib import Path

import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobOptions, Quality
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from fake_engine import FakeEngine, error_body
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
    tree = window.library_panel.tree
    assert tree.topLevelItemCount() == 1
    assert "Robot" in tree.topLevelItem(0).child(0).text(0)
    tree.setCurrentItem(tree.topLevelItem(0).child(0))
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
    assert "skeleton" in win.library_panel.tree.topLevelItem(0).child(0).text(0)


def test_library_rescans_when_app_becomes_active(qtbot, window, png, qapp):
    add_succeeded(Library(window.vm.library.root), png, "From Blender")
    assert window.library_panel.tree.topLevelItemCount() == 0
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert window.library_panel.tree.topLevelItemCount() == 1


def test_half_written_entry_is_skipped(qtbot, window, png, qapp):
    add_succeeded(window.vm.library, png, "Robot")
    broken = window.vm.library.root / "20261022T120000-abcdef"
    broken.mkdir()
    (broken / "entry.json").write_text('{"entry_id": "20261022T1200')
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert window.library_panel.tree.topLevelItemCount() == 1


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


@pytest.fixture
def engine():
    fake = FakeEngine()
    fake.start()
    yield fake
    fake.stop()


def make_window(qtbot, tmp_path, make_api, engine) -> MainWindow:
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(state=EngineState.loading)
    api = make_api(engine.url)
    generation = GenerationViewModel(api, library, supervisor, Settings())
    checks = lambda _paths: []
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=checks)
    engine.controller.library_dir = paths.library
    vm.poll_ms = 20
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


def test_export_fbx_writes_file_and_reports(qtbot, tmp_path, make_api, png, engine):
    engine.controller.export_file = b"data"
    win = make_window(qtbot, tmp_path, make_api, engine)
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


def test_failed_export_shows_warning(qtbot, tmp_path, make_api, png, monkeypatch, engine):
    engine.controller.export_answers = [(409, error_body("export_not_ready", "Export stl failed: nope"))]
    win = make_window(qtbot, tmp_path, make_api, engine)
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


def test_obj_export_of_rigged_model_warns_about_skeleton(qtbot, tmp_path, make_api, png, engine):
    engine.controller.export_file = b"data"
    win = make_window(qtbot, tmp_path, make_api, engine)
    entry = add_rigged(win.vm.library, png)
    win.vm.select(entry.entry_id)
    win.ask_export_path = lambda start: (str(tmp_path / "robot"), "Wavefront OBJ (*.obj)")
    drops = []
    win.warn_format_drops = lambda found: drops.append(set(found))
    with qtbot.waitSignal(win.vm.export_finished, timeout=5000):
        win.generation_panel.save_button.click()
    assert drops == [{"skeleton"}]
    assert (tmp_path / "robot.obj").exists()


def test_add_walk_asks_with_the_detected_body(qtbot, tmp_path, make_api, png, engine):
    win = make_window(qtbot, tmp_path, make_api, engine)
    entry = add_rigged(win.vm.library, png)
    asked = []
    win.ask_body_type = lambda detected: asked.append(detected)  # cancel: no walk
    with qtbot.waitSignal(win.vm.body_detected, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert asked == ["biped"]


def test_chosen_body_starts_the_walk(qtbot, tmp_path, make_api, png, engine):
    win = make_window(qtbot, tmp_path, make_api, engine)
    entry = add_rigged(win.vm.library, png)
    win.ask_body_type = lambda detected: "quadruped"
    walks = []
    win.vm.add_walk = lambda entry_id, body: walks.append((entry_id, body))
    with qtbot.waitSignal(win.vm.body_detected, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert walks == [(entry.entry_id, "quadruped")]


def test_cancelled_dialog_starts_no_walk(qtbot, tmp_path, make_api, png, engine):
    win = make_window(qtbot, tmp_path, make_api, engine)
    entry = add_rigged(win.vm.library, png)
    win.ask_body_type = lambda detected: None
    walks = []
    win.vm.add_walk = lambda entry_id, body: walks.append((entry_id, body))
    with qtbot.waitSignal(win.vm.body_detected, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert walks == []


def test_failed_detection_shows_warning(qtbot, tmp_path, make_api, png, monkeypatch, engine):
    engine.controller.body_answer = (409, error_body("no_skeleton", "broken"))
    win = make_window(qtbot, tmp_path, make_api, engine)
    entry = add_rigged(win.vm.library, png)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    with qtbot.waitSignal(win.vm.walk_failed, timeout=5000):
        win.library_panel.add_walk_requested.emit(entry.entry_id)
    assert len(warnings) == 1
    assert "Could not add the walk animation" in warnings[0][2] and "broken" in warnings[0][2]
    assert win.vm.library.entries() and len(win.vm.library.entries()) == 1


def add_rigged_version(library, root, name="Robot rigged"):
    derived = library.create_derived(root.entry_id)
    library.model_path(derived.entry_id).write_bytes(b"glTF")
    derived = derived.model_copy(update={"outcome": Outcome.succeeded, "name": name, "options": JobOptions(rig=True)})
    library.update(derived)
    return derived


def test_library_groups_versions_under_one_character(window, png):
    root = add_succeeded(window.vm.library, png, "Robot")
    derived = add_rigged_version(window.vm.library, root)
    window.vm.entries_changed.emit()
    tree = window.library_panel.tree
    assert tree.topLevelItemCount() == 1
    top = tree.topLevelItem(0)
    assert top.text(0).startswith("Robot\n")
    assert top.childCount() == 2
    assert [top.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(2)] == [root.entry_id, derived.entry_id]
    assert "skeleton" in top.child(1).text(0)


def test_selecting_a_version_shows_its_model(window, png):
    root = add_succeeded(window.vm.library, png, "Robot")
    derived = add_rigged_version(window.vm.library, root)
    window.vm.entries_changed.emit()
    top = window.library_panel.tree.topLevelItem(0)
    window.library_panel.tree.setCurrentItem(top.child(0))
    assert window.vm.selected.entry_id == root.entry_id
    assert window.viewer.shown == window.vm.library.model_path(root.entry_id)
    tree = window.library_panel.tree
    tree.setCurrentItem(tree.topLevelItem(0).child(1))
    assert window.vm.selected.entry_id == derived.entry_id
    assert window.viewer.shown == window.vm.library.model_path(derived.entry_id)


def test_selecting_a_character_selects_its_newest_finished_version(window, png):
    root = add_succeeded(window.vm.library, png, "Robot")
    derived = add_rigged_version(window.vm.library, root)
    window.vm.entries_changed.emit()
    window.library_panel.tree.setCurrentItem(window.library_panel.tree.topLevelItem(0))
    assert window.vm.selected.entry_id == derived.entry_id


def test_character_menu_has_its_four_actions(window, png):
    add_succeeded(window.vm.library, png, "Robot")
    window.vm.entries_changed.emit()
    names = [a.objectName() for a in window.library_panel.build_character_menu().actions()]
    assert names == ["voice_phrases", "export_character", "rename", "delete"]


def test_import_button_emits_its_signal(qtbot, window, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))  # cancel: no modal dialog in tests
    with qtbot.waitSignal(window.library_panel.import_character_requested, timeout=1000):
        window.library_panel.import_button.click()


def character_window(qtbot, tmp_path, make_api, **kwargs) -> MainWindow:
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor(state=EngineState.loading)
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=lambda _paths: [])
    win = MainWindow(vm, viewer=StubViewer(), **kwargs)
    win.ask_close_anyway = lambda: True
    qtbot.addWidget(win)
    return win


def test_character_request_opens_its_dialog(qtbot, tmp_path, make_api):
    asked, dialogs = [], []

    def factory(character_id):
        asked.append(character_id)
        dialog = QDialog()
        qtbot.addWidget(dialog)
        dialogs.append(dialog)
        return dialog

    win = character_window(qtbot, tmp_path, make_api, character_dialog_factory=factory)
    win.library_panel.character_requested.emit("char-1")
    assert asked == ["char-1"]
    assert dialogs[0].isVisible()
    dialogs[0].close()
    assert win._character_dialogs == []


def test_character_request_without_factory_does_nothing(qtbot, tmp_path, make_api):
    win = character_window(qtbot, tmp_path, make_api)
    win.library_panel.character_requested.emit("char-1")
    assert win.statusBar().currentMessage() == ""


def test_export_character_passes_m3dchar_path(qtbot, tmp_path, make_api, png, monkeypatch):
    win = character_window(qtbot, tmp_path, make_api)
    entry = add_succeeded(win.vm.library, png, "Robot")
    asked, exported = [], []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *args: asked.append(args[2]) or (str(tmp_path / "robot"), "Character packages (*.m3dchar)"))
    monkeypatch.setattr(win.vm, "export_character", lambda cid, target: exported.append((cid, target)))
    win.library_panel.export_character_requested.emit(entry.entry_id)
    assert asked == [str(Path(win.vm.settings.export_dir) / "Robot.m3dchar")]
    assert exported == [(entry.entry_id, tmp_path / "robot.m3dchar")]


def test_export_character_cancel_exports_nothing(qtbot, tmp_path, make_api, png, monkeypatch):
    win = character_window(qtbot, tmp_path, make_api)
    entry = add_succeeded(win.vm.library, png, "Robot")
    exported = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    monkeypatch.setattr(win.vm, "export_character", lambda cid, target: exported.append((cid, target)))
    win.library_panel.export_character_requested.emit(entry.entry_id)
    assert exported == []


def test_import_character_passes_chosen_package(qtbot, tmp_path, make_api, monkeypatch):
    win = character_window(qtbot, tmp_path, make_api)
    imported = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args: (str(tmp_path / "robot.m3dchar"), "Character packages (*.m3dchar)"))
    monkeypatch.setattr(win.vm, "import_character", lambda package: imported.append(package))
    win.library_panel.import_character_requested.emit()
    assert imported == [tmp_path / "robot.m3dchar"]


def test_import_character_cancel_imports_nothing(qtbot, tmp_path, make_api, monkeypatch):
    win = character_window(qtbot, tmp_path, make_api)
    imported = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(win.vm, "import_character", lambda package: imported.append(package))
    win.library_panel.import_character_requested.emit()
    assert imported == []


def test_character_failure_shows_warning(qtbot, tmp_path, make_api, monkeypatch):
    win = character_window(qtbot, tmp_path, make_api)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    win.vm.character_failed.emit("boom")
    assert len(warnings) == 1
    assert warnings[0][1] == "Character" and warnings[0][2] == "boom"


def test_character_export_reports_its_path(qtbot, tmp_path, make_api):
    win = character_window(qtbot, tmp_path, make_api)
    win.vm.character_exported.emit(Path("/x/a.m3dchar"))
    assert win.statusBar().currentMessage() == "Character exported to /x/a.m3dchar"


def model_voice_window(qtbot, tmp_path, make_api):
    from model3d_client.viewmodels.characters import CharacterViewModel
    from model3d_client.views.model_voice_panel import ModelVoicePanel

    class Player:
        def play(self, path):
            pass

    holder = {}

    def make_panel(win_api, library):
        vm = CharacterViewModel(win_api, library, None, Player(), tmp_path / "cache")
        holder["vm"] = vm
        return ModelVoicePanel(vm)

    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    api = make_api("http://127.0.0.1:9")
    supervisor = FakeSupervisor(state=EngineState.loading)
    generation = GenerationViewModel(api, library, supervisor, Settings())
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=lambda _paths: [])
    win = MainWindow(vm, viewer=StubViewer(), model_voice_panel=make_panel(api, library))
    win.ask_close_anyway = lambda: True
    qtbot.addWidget(win)
    return win


def test_selecting_a_version_points_the_voice_area_at_its_character(qtbot, tmp_path, make_api, png):
    win = model_voice_window(qtbot, tmp_path, make_api)
    root = add_succeeded(win.vm.library, png, "Robot")
    derived = add_rigged_version(win.vm.library, root)
    win.vm.entries_changed.emit()
    assert win.model_voice_panel.vm.character_id is None
    win.vm.select(derived.entry_id)
    assert win.model_voice_panel.vm.character_id == root.entry_id
    win.vm.select(None)
    assert win.model_voice_panel.vm.character_id is None


def test_character_request_selects_it_and_reveals_the_voice_area(qtbot, tmp_path, make_api, png):
    win = model_voice_window(qtbot, tmp_path, make_api)
    root = add_succeeded(win.vm.library, png, "Robot")
    derived = add_rigged_version(win.vm.library, root)
    win.vm.entries_changed.emit()
    win.show()
    win.library_panel.character_requested.emit(root.entry_id)
    assert win.vm.selected.entry_id == derived.entry_id
    assert win.model_voice_panel.vm.character_id == root.entry_id
    assert win.model_voice_panel.isVisible()


def test_selecting_a_character_does_not_grow_the_window(qtbot, tmp_path, make_api, png):
    win = model_voice_window(qtbot, tmp_path, make_api)
    win.resize(1400, 800)
    win.show()
    qtbot.waitExposed(win)
    root = add_succeeded(win.vm.library, png, "Robot")
    add_rigged_version(win.vm.library, root)
    win.vm.entries_changed.emit()
    qtbot.wait(10)
    min_before = win.minimumSizeHint().height()
    win.library_panel.tree.setCurrentItem(win.library_panel.tree.topLevelItem(0))
    qtbot.wait(50)
    assert win.minimumSizeHint().height() <= min_before
    assert win.height() == 800


def test_sharper_texture_check_follows_the_engine(qtbot, tmp_path, make_api):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    for available in (False, True):
        supervisor = FakeSupervisor(sharper_texture=available)
        api = make_api("http://127.0.0.1:9")
        library = Library(paths.library)
        generation = GenerationViewModel(api, library, supervisor, Settings())
        win = MainWindow(MainViewModel(paths, api, supervisor, library, generation, Settings()), viewer=StubViewer())
        qtbot.addWidget(win)
        check = win.generation_panel.sharper_check
        assert check.text() == "Sharper texture"
        assert check.isEnabled() is available
        assert check.toolTip() == ("Sharper texture needs the TRELLIS.2 generator" if not available else check.toolTip())
        assert check.toolTip()
