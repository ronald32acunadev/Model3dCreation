import json

import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobOptions

from fake_engine import FakeEngine, error_body
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


@pytest.fixture
def with_engine(tmp_path, make_api):
    engine = FakeEngine()
    engine.start()
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    library = Library(paths.library)
    supervisor = FakeSupervisor()
    api = make_api(engine.url)
    generation = GenerationViewModel(api, library, supervisor, Settings())
    vm = MainViewModel(paths, api, supervisor, library, generation, Settings(), checks=lambda _paths: [])
    vm.poll_ms = 20
    engine.controller.library_dir = paths.library
    yield vm, library, engine
    engine.stop()


def rigged(library, png):
    entry = library.create(png, JobOptions(rig=True))
    library.model_path(entry.entry_id).write_bytes(b"glTF model")
    stats = ResultStats(vertices=1, faces=2, textured=True, duration_s=3.0)
    entry = entry.model_copy(update={"outcome": Outcome.succeeded, "stats": stats})
    library.update(entry)
    return entry


def test_export_fbx_downloads_the_finished_file_to_the_target(qtbot, tmp_path, with_engine, png):
    vm, library, engine = with_engine
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    engine.controller.export_answers = [(409, error_body("export_not_ready", "Export fbx is running"))]
    engine.controller.export_file = b"fbx data"
    target = tmp_path / "out" / "penguin.fbx"
    target.parent.mkdir(parents=True, exist_ok=True)
    states = []
    vm.export_state_changed.connect(lambda: states.append(vm.exporting))
    with qtbot.waitSignal(vm.export_finished, timeout=5000) as blocker:
        vm.export(target)
    assert blocker.args == [target]
    assert target.read_bytes() == b"fbx data"
    assert engine.controller.export_posts == [(entry.entry_id, "fbx")]
    assert len(engine.controller.export_gets) == 2
    assert states[0] is True and states[-1] is False
    assert vm.exporting is False


def test_failed_export_reports_the_engine_message(qtbot, tmp_path, with_engine, png):
    vm, library, engine = with_engine
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    engine.controller.export_answers = [(409, error_body("export_not_ready", "Export stl failed: Could not write /x/model.stl"))]
    target = tmp_path / "out" / "penguin.stl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with qtbot.waitSignal(vm.export_failed, timeout=5000) as blocker:
        vm.export(target)
    assert blocker.args == ["Could not write /x/model.stl"]
    assert not target.exists()
    assert vm.exporting is False


def test_export_rejected_by_the_engine_reports_failure(qtbot, tmp_path, with_engine, png):
    vm, library, engine = with_engine
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    engine.controller.export_start = (409, error_body("entry_busy", "A tool is already running for this entry"))
    target = tmp_path / "out" / "penguin.fbx"
    target.parent.mkdir(parents=True, exist_ok=True)
    with qtbot.waitSignal(vm.export_failed, timeout=5000) as blocker:
        vm.export(target)
    assert blocker.args == ["A tool is already running for this entry"]
    assert vm.exporting is False
    assert engine.controller.export_gets == []


def test_export_zip_is_extracted_next_to_the_target(qtbot, tmp_path, with_engine, png):
    vm, library, engine = with_engine
    entry = succeeded(library, png)
    vm.select(entry.entry_id)
    engine.controller.export_zip = {"model.obj": b"mtllib model.mtl\no a\n", "model.mtl": b"newmtl a\n", "tex.png": b"png"}
    target = tmp_path / "out" / "robot.obj"
    target.parent.mkdir(parents=True, exist_ok=True)
    with qtbot.waitSignal(vm.export_finished, timeout=5000) as blocker:
        vm.export(target)
    assert blocker.args == [target]
    assert target.read_bytes() == b"mtllib robot.mtl\no a\n"
    assert (tmp_path / "out" / "robot.mtl").read_bytes() == b"newmtl a\n"
    assert (tmp_path / "out" / "tex.png").read_bytes() == b"png"
    assert not (tmp_path / "out" / "model.obj").exists()


def test_export_drops_follow_format(with_engine, png):
    vm, library, _ = with_engine
    vm.select(rigged(library, png).entry_id)
    assert vm.export_drops(".obj") == {"skeleton"}
    assert vm.export_drops(".stl") == {"skeleton", "texture"}
    assert vm.export_drops(".fbx") == set()
    assert vm.export_drops(".usdz") == set()
    assert vm.export_drops(".glb") == set()


def test_export_drops_empty_without_selection(with_engine):
    vm, _, _ = with_engine
    assert vm.export_drops(".stl") == set()


def test_export_unsupported_suffix_raises(with_engine, png, tmp_path):
    vm, library, _ = with_engine
    vm.select(succeeded(library, png).entry_id)
    with pytest.raises(ValueError):
        vm.export(tmp_path / "penguin.png")


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


def test_detect_body_emits_body_detected(qtbot, with_engine, png):
    vm, library, engine = with_engine
    entry = rigged(library, png)
    with qtbot.waitSignal(vm.body_detected, timeout=5000) as blocker:
        vm.detect_body(entry.entry_id)
    assert blocker.args == [entry.entry_id, {"body": "biped", "legs": [], "arms": [], "up": 1}]
    assert not vm.animating


def test_detect_body_failure_emits_walk_failed(qtbot, with_engine, png):
    vm, library, engine = with_engine
    entry = rigged(library, png)
    engine.controller.body_answer = (409, error_body("no_skeleton", "Library entry has no skeleton"))
    with qtbot.waitSignal(vm.walk_failed, timeout=5000) as blocker:
        vm.detect_body(entry.entry_id)
    assert blocker.args == ["Library entry has no skeleton"]
    assert not vm.animating


def test_animating_blocks_add_walk_and_state_is_reported(qtbot, with_engine, png):
    vm, library, engine = with_engine
    entry = rigged(library, png)
    states = []
    vm.animation_state_changed.connect(lambda: states.append(vm.animating))
    with qtbot.waitSignal(vm.body_detected, timeout=5000):
        vm.detect_body(entry.entry_id)
        assert vm.animating is True
        assert not vm.can_add_walk(entry)
    assert states == [True, False]


def test_add_walk_creates_named_entry_and_selects_it(qtbot, with_engine, png):
    vm, library, engine = with_engine
    source = rigged(library, png)
    created = []

    def handler(entry_id, body):
        derived = library.create_derived(entry_id, suffix=" (walk)")
        created.append(derived)
        return (202, {**json.loads(derived.model_dump_json()), "files": []})

    engine.controller.walk = handler
    vm.select(source.entry_id)
    with qtbot.waitSignal(vm.walk_added, timeout=5000) as blocker:
        vm.add_walk(source.entry_id, "biped")
        qtbot.waitUntil(lambda: len(created) > 0, timeout=5000)
        library.model_path(created[0].entry_id).write_bytes(b"walk data")
        library.update(created[0].model_copy(update={"outcome": Outcome.succeeded, "stats": source.stats, "seed": source.seed, "options": source.options}))
    added = blocker.args[0]
    assert added.name == "penguin (walk)"
    assert added.outcome == Outcome.succeeded
    assert added.source_entry_id == source.entry_id
    assert vm.selected.entry_id == added.entry_id
    assert library.model_path(added.entry_id).read_bytes() == b"walk data"
    assert engine.controller.walk_posts == [(source.entry_id, "biped")]
    assert source in library.entries()
    assert len(library.entries()) == 2
    assert not vm.animating


def test_add_walk_failure_removes_the_new_entry(qtbot, with_engine, png):
    vm, library, engine = with_engine
    source = rigged(library, png)
    created = []

    def handler(entry_id, body):
        derived = library.create_derived(entry_id, suffix=" (walk)")
        created.append(derived)
        return (202, {**json.loads(derived.model_dump_json()), "files": []})

    engine.controller.walk = handler
    vm.select(source.entry_id)
    with qtbot.waitSignal(vm.walk_failed, timeout=5000) as blocker:
        vm.add_walk(source.entry_id, "biped")
        qtbot.waitUntil(lambda: len(created) > 0, timeout=5000)
        library.update(created[0].model_copy(update={"outcome": Outcome.failed, "error": JobError(code="walk_failed", message="The rig has no legs")}))
    assert blocker.args == ["The rig has no legs"]
    assert [e.entry_id for e in library.entries()] == [source.entry_id]
    assert not vm.animating


def test_add_walk_rejected_by_the_engine_reports_failure(qtbot, with_engine, png):
    vm, library, engine = with_engine
    source = rigged(library, png)
    vm.select(source.entry_id)
    with qtbot.waitSignal(vm.walk_failed, timeout=5000) as blocker:
        vm.add_walk(source.entry_id, "biped")
    assert blocker.args == ["no_skeleton"]
    assert len(library.entries()) == 1
    assert not vm.animating
