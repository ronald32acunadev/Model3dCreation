import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobOptions

from fakes import FakeSupervisor
from model3d_client.paths import AppPaths
from model3d_client.services.diagnostics import Check, CheckId
from model3d_client.services.library import Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState
from model3d_client.viewmodels.generation import GenerationViewModel, Phase
from model3d_client.viewmodels.main import CloseDecision, MainViewModel


@pytest.fixture
def setup(tmp_path, make_api):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor()
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    checks = lambda _paths: [Check(CheckId.gpu, True, "GPU 0"), Check(CheckId.weights, False, "missing")]
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=checks)
    return vm, library, supervisor, generation


def succeeded(library, png):
    entry = library.create(png, JobOptions())
    library.model_path(entry.entry_id).write_bytes(b"glTF model")
    stats = ResultStats(vertices=1, faces=2, textured=True, duration_s=3.0)
    entry = entry.model_copy(update={"outcome": Outcome.succeeded, "stats": stats})
    library.update(entry)
    return entry


def test_select_and_delete_selected_clears_selection(qtbot, setup, png):
    vm, library, _, _ = setup
    entry = succeeded(library, png)
    with qtbot.waitSignal(vm.selection_changed):
        vm.select(entry.entry_id)
    assert vm.selected.entry_id == entry.entry_id
    assert vm.selected_model() == library.model_path(entry.entry_id)
    with qtbot.waitSignal(vm.entries_changed):
        vm.delete(entry.entry_id)
    assert vm.selected is None
    assert vm.entries() == []


def test_rename_updates_entries(qtbot, setup, png):
    vm, library, _, _ = setup
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    with qtbot.waitSignal(vm.entries_changed):
        vm.rename(entry.entry_id, "Robot")
    assert vm.entries()[0].name == "Robot"
    assert vm.selected.name == "Robot"


def test_failed_entry_has_no_model(setup, png):
    vm, library, _, _ = setup
    entry = library.create(png, JobOptions())
    library.update(entry.model_copy(update={"outcome": Outcome.failed}))
    vm.select(entry.entry_id)
    assert vm.selected_model() is None


def test_export_copies_glb(setup, png, tmp_path):
    vm, library, _, _ = setup
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    target = tmp_path / "out" / "penguin.glb"
    target.parent.mkdir()
    vm.export(target)
    assert target.read_bytes() == b"glTF model"


def test_export_without_model_raises(setup, tmp_path):
    vm, _, _, _ = setup
    with pytest.raises(ValueError):
        vm.export(tmp_path / "x.glb")


def test_finished_success_selects_new_entry(qtbot, setup, png):
    vm, library, _, generation = setup
    entry = succeeded(library, png)
    with qtbot.waitSignal(vm.selection_changed):
        generation.finished.emit(entry)
    assert vm.selected.entry_id == entry.entry_id


def test_close_request_confirms_while_generating(setup):
    vm, _, _, generation = setup
    assert vm.close_request() == CloseDecision.close
    generation.phase = Phase.shaping
    assert vm.close_request() == CloseDecision.confirm


def test_shutdown_marks_running_entry_interrupted(setup, png):
    vm, library, supervisor, generation = setup
    generation.entry = library.create(png, JobOptions())
    done = []
    vm.shutdown(lambda: done.append(True))
    assert done == [True] and supervisor.stopped == 1
    assert library.entries()[0].outcome == Outcome.interrupted


def test_diagnostics_shown_on_crash(qtbot, setup):
    vm, _, supervisor, _ = setup
    assert vm.show_diagnostics is False
    supervisor.failure = "The engine exited with code 1"
    with qtbot.waitSignal(vm.diagnostics_changed, timeout=5000):
        supervisor.set_state(EngineState.crashed)
    assert vm.show_diagnostics is True
    by_id = {check.id: check for check in vm.diagnostics}
    assert by_id[CheckId.weights].ok is False
    assert by_id[CheckId.engine].detail == "The engine exited with code 1"
    with qtbot.waitSignal(vm.diagnostics_changed, timeout=5000):
        supervisor.set_state(EngineState.ready)
    assert vm.show_diagnostics is False


def test_restart_engine(setup):
    vm, _, supervisor, _ = setup
    vm.restart_engine()
    assert supervisor.restarted == 1
