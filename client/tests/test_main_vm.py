import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobOptions

from fakes import FakeSupervisor
from model3d_client.paths import AppPaths
from model3d_client.services.converter import ModelConverter
from model3d_client.services.animator import RigAnimator
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


OK_PYTHON = '#!/bin/sh\nprintf data > "$3"\nexit 0\n'


@pytest.fixture
def converting(tmp_path, make_api):
    python = tmp_path / "python"
    python.write_text(OK_PYTHON)
    python.chmod(0o755)
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor()
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(),
                       checks=lambda _paths: [], converter=ModelConverter(python))
    return vm, library


def rigged(library, png):
    entry = library.create(png, JobOptions(rig=True))
    library.model_path(entry.entry_id).write_bytes(b"glTF model")
    stats = ResultStats(vertices=1, faces=2, textured=True, duration_s=3.0)
    entry = entry.model_copy(update={"outcome": Outcome.succeeded, "stats": stats})
    library.update(entry)
    return entry


def test_export_fbx_converts_and_reports(qtbot, converting, png, tmp_path):
    vm, library = converting
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    target = tmp_path / "out" / "penguin.fbx"
    target.parent.mkdir()
    states = []
    vm.export_state_changed.connect(lambda: states.append(vm.exporting))
    with qtbot.waitSignal(vm.export_finished, timeout=5000) as blocker:
        vm.export(target)
    assert blocker.args == [target]
    assert target.read_bytes() == b"data"
    assert states and states[-1] is False


def test_export_drops_follow_format(converting, png):
    vm, library = converting
    vm.select(rigged(library, png).entry_id)
    assert vm.export_drops(".obj") == {"skeleton"}
    assert vm.export_drops(".stl") == {"skeleton", "texture"}
    assert vm.export_drops(".fbx") == set()
    assert vm.export_drops(".usdz") == set()
    assert vm.export_drops(".glb") == set()


def test_export_drops_empty_without_selection(converting):
    vm, _ = converting
    assert vm.export_drops(".stl") == set()


def test_export_unsupported_suffix_raises(converting, png, tmp_path):
    vm, library = converting
    vm.select(succeeded(library, png).entry_id)
    with pytest.raises(ValueError):
        vm.export(tmp_path / "penguin.png")


ANIMATE_OK = """case "$2" in
detect) echo 'MODEL3D_RESULT {"body": "biped", "legs": [], "arms": [], "up": 1}' ;;
walk) printf data > "$4"; echo 'MODEL3D_RESULT {"body": "biped"}' ;;
esac
exit 0
"""
ANIMATE_FAIL = "echo broken >&2\nexit 1\n"
ANIMATE_SLOW = "sleep 0.5\necho 'MODEL3D_RESULT {\"body\": \"biped\"}'\nexit 0\n"


def animating_vm(tmp_path, make_api, body):
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n" + body)
    python.chmod(0o755)
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor()
    api = make_api("http://127.0.0.1:9")
    generation = GenerationViewModel(api, library, supervisor, Settings())
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(),
                       checks=lambda _paths: [], animator=RigAnimator(python))
    return vm, library


def test_can_add_walk_needs_a_rigged_succeeded_model(setup, png):
    vm, library, _, _ = setup
    plain = succeeded(library, png)
    assert not vm.can_add_walk(plain)
    assert vm.can_add_walk(rigged(library, png))


def test_can_add_walk_false_without_model_file(setup, png):
    vm, library, _, _ = setup
    entry = rigged(library, png)
    library.model_path(entry.entry_id).unlink()
    assert not vm.can_add_walk(entry)


def test_detect_body_emits_body_detected(qtbot, tmp_path, make_api, png):
    vm, library = animating_vm(tmp_path, make_api, ANIMATE_OK)
    entry = rigged(library, png)
    with qtbot.waitSignal(vm.body_detected, timeout=5000) as blocker:
        vm.detect_body(entry.entry_id)
    assert blocker.args == [entry.entry_id, {"body": "biped", "legs": [], "arms": [], "up": 1}]
    assert not vm.animating


def test_animating_blocks_add_walk_and_state_is_reported(qtbot, tmp_path, make_api, png):
    vm, library = animating_vm(tmp_path, make_api, ANIMATE_SLOW)
    entry = rigged(library, png)
    states = []
    vm.animation_state_changed.connect(lambda: states.append(vm.animating))
    with qtbot.waitSignal(vm.body_detected, timeout=5000):
        vm.detect_body(entry.entry_id)
        assert vm.animating and not vm.can_add_walk(entry)
    assert states == [True, False]


def test_add_walk_creates_named_entry_and_selects_it(qtbot, tmp_path, make_api, png):
    vm, library = animating_vm(tmp_path, make_api, ANIMATE_OK)
    source = rigged(library, png)
    vm.select(source.entry_id)
    with qtbot.waitSignal(vm.walk_added, timeout=5000) as blocker:
        vm.add_walk(source.entry_id, "biped")
    added = blocker.args[0]
    assert added.name == "penguin (walk)" and added.outcome == Outcome.succeeded
    assert added.source_entry_id == source.entry_id
    assert vm.selected.entry_id == added.entry_id
    assert library.model_path(added.entry_id).read_bytes() == b"data"
    kept = next(e for e in library.entries() if e.entry_id == source.entry_id)
    assert kept == source and len(library.entries()) == 2
    assert not vm.animating


def test_add_walk_failure_leaves_no_entry(qtbot, tmp_path, make_api, png):
    vm, library = animating_vm(tmp_path, make_api, ANIMATE_FAIL)
    source = rigged(library, png)
    with qtbot.waitSignal(vm.walk_failed, timeout=5000) as blocker:
        vm.add_walk(source.entry_id, "quadruped")
    assert "broken" in blocker.args[0]
    assert [e.entry_id for e in library.entries()] == [source.entry_id]
    assert not vm.animating
