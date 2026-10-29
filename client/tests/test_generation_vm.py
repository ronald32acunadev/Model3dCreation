import pytest
from model3d_contracts.jobs import JobState, Quality

from fake_engine import CLOSE, FakeEngine, error_body, make_job
from fakes import FakeSupervisor
from model3d_client.services.library import Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState
from model3d_client.viewmodels.generation import GenerationViewModel, Phase

TS = "2026-10-21T10:00:00Z"
STATS = {"vertices": 100, "faces": 200, "textured": True, "duration_s": 135.0}


def progress(stage="shape", value=0.5, message="Denoising step 25 of 50"):
    return {"type": "progress", "job_id": "J1", "ts": TS, "stage": stage, "progress": value, "message": message}


def completed():
    return {"type": "completed", "job_id": "J1", "ts": TS, "result_url": "/v1/jobs/J1/result", "stats": STATS}


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "library")


@pytest.fixture
def make_vm(make_api, engine, library):
    def make(supervisor=None, settings=None):
        return GenerationViewModel(make_api(engine.url), library, supervisor or FakeSupervisor(), settings or Settings())

    return make


def run_to_end(qtbot, vm):
    done = []
    vm.finished.connect(done.append)
    vm.generate()
    qtbot.waitUntil(lambda: bool(done), timeout=10000)
    return done[0]


def test_generate_happy_path_saves_model_and_entry(qtbot, engine, make_vm, library, png):
    engine.controller.push(progress("shape", 0.5), progress("paint", 0.2), completed(), CLOSE)
    vm = make_vm()
    vm.set_image(png)
    assert vm.can_generate
    entry = run_to_end(qtbot, vm)
    assert entry.outcome == Outcome.succeeded
    assert (entry.job_id, entry.seed) == ("J1", 42)
    assert entry.stats.duration_s == 135.0
    assert library.model_path(entry.entry_id).read_bytes() == b"glTF fake model"
    assert library.entries()[0] == entry
    assert vm.phase == Phase.idle and vm.can_generate
    assert vm.error is None


def test_progress_updates_phase_and_message(qtbot, engine, make_vm, png):
    engine.controller.push(progress("paint", 0.25, "Painting views"))
    vm = make_vm()
    vm.set_image(png)
    vm.generate()
    qtbot.waitUntil(lambda: vm.phase == Phase.painting, timeout=10000)
    assert (vm.progress, vm.message) == (0.25, "Painting views")
    assert not vm.can_generate and vm.can_cancel
    engine.controller.push(completed(), CLOSE)
    qtbot.waitUntil(lambda: vm.phase == Phase.idle, timeout=10000)


def test_options_follow_settings_and_panel(qtbot, engine, make_vm, png):
    engine.controller.push(completed(), CLOSE)
    vm = make_vm(settings=Settings(default_texture=False, default_quality=Quality.draft))
    assert (vm.texture, vm.quality) == (False, Quality.draft)
    vm.set_quality(Quality.final)
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.options.texture is False and entry.options.quality == Quality.final
    assert '"quality":"final"' in engine.controller.submissions[0]["options"].replace(" ", "")


def test_failed_event_records_error(qtbot, engine, make_vm, png):
    engine.controller.push(
        {"type": "failed", "job_id": "J1", "ts": TS, "error": {"code": "gpu_out_of_memory", "message": "OOM"}}, CLOSE
    )
    vm = make_vm()
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.outcome == Outcome.failed
    assert entry.error.code == "gpu_out_of_memory"
    assert vm.error.code == "gpu_out_of_memory"


def test_cancel_shows_cancelling_until_cancelled_event(qtbot, engine, make_vm, png):
    engine.controller.push(progress("paint", 0.1))
    vm = make_vm()
    vm.set_image(png)
    done = []
    vm.finished.connect(done.append)
    vm.generate()
    qtbot.waitUntil(lambda: vm.phase == Phase.painting, timeout=10000)
    vm.cancel()
    assert vm.phase == Phase.cancelling and not vm.can_cancel
    qtbot.waitUntil(lambda: engine.controller.cancels == ["J1"], timeout=5000)
    engine.controller.push(progress("paint", 0.5))  # the engine finishes the stage first
    qtbot.wait(200)
    assert vm.phase == Phase.cancelling
    engine.controller.push({"type": "cancelled", "job_id": "J1", "ts": TS}, CLOSE)
    qtbot.waitUntil(lambda: bool(done), timeout=10000)
    assert done[0].outcome == Outcome.cancelled
    assert vm.phase == Phase.idle


def test_cancel_job_finished_is_ignored(qtbot, engine, make_vm, png):
    engine.controller.cancel = (409, error_body("job_finished"))
    engine.controller.push(progress())
    vm = make_vm()
    vm.set_image(png)
    done = []
    vm.finished.connect(done.append)
    vm.generate()
    qtbot.waitUntil(lambda: vm.phase == Phase.shaping, timeout=10000)
    vm.cancel()
    qtbot.waitUntil(lambda: engine.controller.cancels == ["J1"], timeout=5000)
    engine.controller.push(completed(), CLOSE)
    qtbot.waitUntil(lambda: bool(done), timeout=10000)
    assert done[0].outcome == Outcome.succeeded


def test_texture_disabled_in_shape_only_mode(qtbot, engine, make_vm, png):
    engine.controller.push(completed(), CLOSE)
    vm = make_vm(supervisor=FakeSupervisor(texture=False))
    assert vm.texture_available is False
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.options.texture is False


def test_submit_503_records_failed_entry(qtbot, engine, make_vm, png):
    engine.controller.submit = (503, error_body("engine_not_ready", "still loading"))
    vm = make_vm()
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.outcome == Outcome.failed
    assert (entry.error.code, entry.error.message) == ("engine_not_ready", "still loading")
    assert entry.job_id is None


def test_generate_disabled_until_engine_ready(qtbot, make_vm, png):
    supervisor = FakeSupervisor(state=EngineState.loading)
    vm = make_vm(supervisor=supervisor)
    vm.set_image(png)
    assert not vm.can_generate
    with qtbot.waitSignal(vm.changed, timeout=1000):
        supervisor.set_state(EngineState.ready)
    assert vm.can_generate


def test_socket_closed_without_final_event_fails_job(qtbot, engine, make_vm, png):
    engine.controller.push(progress(), CLOSE)  # engine died mid-job; GET /v1/jobs/J1 answers 404
    vm = make_vm()
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.outcome == Outcome.failed and entry.error.code == "engine_lost"
    assert vm.phase == Phase.idle


def test_socket_closed_after_job_succeeded_downloads_result(qtbot, engine, make_vm, png):
    engine.controller.jobs["J1"] = make_job("J1", JobState.succeeded)
    engine.controller.push(progress(), CLOSE)
    vm = make_vm()
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.outcome == Outcome.succeeded


def test_rejects_unsupported_or_oversized_image(tmp_path, make_vm):
    vm = make_vm()
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a")
    vm.set_image(gif)
    assert vm.image_problem == "unsupported_format" and not vm.can_generate
    big = tmp_path / "b.png"
    big.write_bytes(b"\0" * (20 * 1024 * 1024 + 1))
    vm.set_image(big)
    assert vm.image_problem == "too_large" and not vm.can_generate
    vm.set_image(tmp_path / "gone.png")
    assert vm.image_problem == "missing" and not vm.can_generate


def finished_entry(library, png, **options):
    from model3d_contracts.jobs import JobOptions

    entry = library.create(png, JobOptions(**options))
    library.update(entry.model_copy(update={"outcome": Outcome.succeeded}))
    library.model_path(entry.entry_id).write_bytes(b"glTF source model")
    return library._read(entry.entry_id)


def test_generate_sends_rig_option(qtbot, engine, make_vm, png):
    engine.controller.push(completed(), CLOSE)
    vm = make_vm()
    assert vm.rig is False
    vm.set_rig(True)
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.options.rig is True
    assert '"rig":true' in engine.controller.submissions[0]["options"].replace(" ", "")


def test_rig_stage_maps_to_rigging_phase(qtbot, engine, make_vm, png):
    engine.controller.push(progress("rig", 0.45, "Skeleton ready"))
    vm = make_vm()
    vm.set_rig(True)
    vm.set_image(png)
    vm.generate()
    qtbot.waitUntil(lambda: vm.phase == Phase.rigging, timeout=10000)
    assert (vm.progress, vm.message) == (0.45, "Skeleton ready")
    assert vm.can_cancel
    engine.controller.push(completed(), CLOSE)
    qtbot.waitUntil(lambda: vm.phase == Phase.idle, timeout=10000)


def test_add_skeleton_creates_new_entry_and_downloads_result(qtbot, engine, make_vm, library, png):
    source = finished_entry(library, png)
    source_json = (library.folder(source.entry_id) / "entry.json").read_bytes()
    engine.controller.push(progress("rig", 0.5, "Skin weights"), completed(), CLOSE)
    vm = make_vm()
    assert vm.can_add_skeleton(source)
    done = []
    vm.finished.connect(done.append)
    vm.add_skeleton(source.entry_id)
    qtbot.waitUntil(lambda: bool(done), timeout=10000)
    rigged = done[0]
    assert rigged.outcome == Outcome.succeeded
    assert rigged.source_entry_id == source.entry_id and rigged.options.rig is True
    assert engine.controller.rig_submissions[0]["model"] == b"glTF source model"
    assert engine.controller.submissions == []
    assert library.model_path(rigged.entry_id).read_bytes() == b"glTF fake model"
    assert (library.folder(source.entry_id) / "entry.json").read_bytes() == source_json
    assert library.model_path(source.entry_id).read_bytes() == b"glTF source model"


def test_rig_unavailable_disables_rig_and_add_skeleton(qtbot, engine, make_vm, library, png):
    reason = "UniRig weights are missing from data/hf (see docs/setup.md)"
    engine.controller.push(completed(), CLOSE)
    vm = make_vm(supervisor=FakeSupervisor(rig=False, rig_reason=reason))
    assert (vm.rig_available, vm.rig_reason) == (False, reason)
    assert vm.can_add_skeleton(finished_entry(library, png)) is False
    vm.set_rig(True)
    vm.set_image(png)
    entry = run_to_end(qtbot, vm)
    assert entry.options.rig is False


def test_add_skeleton_refused_for_rigged_or_failed_entry(make_vm, library, png):
    vm = make_vm()
    rigged = finished_entry(library, png, rig=True)
    failed = library.create(png, __import__("model3d_contracts.jobs", fromlist=["JobOptions"]).JobOptions())
    library.update(failed.model_copy(update={"outcome": Outcome.failed}))
    assert vm.can_add_skeleton(rigged) is False
    assert vm.can_add_skeleton(library._read(failed.entry_id)) is False
    vm.add_skeleton(rigged.entry_id)
    assert vm.phase == Phase.idle
