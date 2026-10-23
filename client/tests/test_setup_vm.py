from pathlib import Path

import pytest

from model3d_client.setup.checks import FolderCheck, Requirement, RequirementId, Toolchain
from model3d_client.setup.recipe import COMPONENT_SIZES, Sources
from model3d_client.setup.runner import StepFailed
from model3d_client.setup.state import InstallState, read_pointer, save_state
from model3d_client.viewmodels.setup import SetupPage, SetupViewModel

TOOLCHAIN = Toolchain(Path("/usr/local/cuda-13.1/bin/nvcc"), Path("/usr/local/cuda-13.1"), (13, 1), "12.0", 8)


def requirements(failing: set[RequirementId] = frozenset()):
    return [Requirement(rid, rid not in failing, "detail") for rid in RequirementId]


class FakeRunner:
    """Records what it was asked to run; fails once when told to."""

    instances: list["FakeRunner"] = []
    fail_with: StepFailed | None = None

    def __init__(self, install_dir, steps, app_version, components, on_progress, on_log):
        self.install_dir, self.steps, self.components = install_dir, steps, components
        self.on_progress, self.on_log = on_progress, on_log
        self.cancelled = False
        FakeRunner.instances.append(self)

    def run(self):
        self.on_log("working")
        self.on_progress(self.steps[0].id if self.steps else "", "title", 0.5)
        if FakeRunner.fail_with is not None:
            error, FakeRunner.fail_with = FakeRunner.fail_with, None
            raise error

    def cancel(self):
        self.cancelled = True


class FakeStep:
    def __init__(self, step_id, component="base"):
        self.id, self.title, self.component = step_id, step_id, component


def fake_recipe(sources, folder, toolchain, components):
    steps = [FakeStep("python"), FakeStep("weights")]
    if "skeletons" in components:
        steps += [FakeStep("unirig-source", "skeletons"), FakeStep("unirig-weights", "skeletons")]
    return steps


@pytest.fixture
def make_vm(tmp_path):
    FakeRunner.instances, FakeRunner.fail_with = [], None
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    state = {"reqs": requirements()}

    def make(**kwargs):
        defaults = dict(
            sources=Sources(app_root=tmp_path / "opt", uv=tmp_path / "opt/bin/uv", app_version="0.1.0"),
            env=env,
            check=lambda: (state["reqs"], TOOLCHAIN if not any(not r.ok for r in state["reqs"]) else None),
            folder_check=lambda folder, needed: FolderCheck(True, None, 10**13, needed),
            runner_factory=FakeRunner,
            recipe=fake_recipe,
            background=lambda work: work(),
        )
        defaults.update(kwargs)
        return SetupViewModel(**defaults)

    make.env, make.state = env, state
    return make


def walk_to_folder(vm):
    vm.next()  # requirements, checked at once
    vm.next()  # components
    vm.next()  # folder


def test_starts_on_welcome(make_vm):
    vm = make_vm()
    assert vm.page == SetupPage.welcome
    assert vm.components == ["base", "skeletons"]


def test_failed_driver_blocks_requirements_page(make_vm):
    make_vm.state["reqs"] = requirements({RequirementId.driver})
    vm = make_vm()
    vm.next()
    assert vm.page == SetupPage.requirements
    assert not vm.can_continue
    vm.next()
    assert vm.page == SetupPage.requirements


def test_recheck_after_fix_unblocks(make_vm):
    make_vm.state["reqs"] = requirements({RequirementId.cuda_toolkit})
    vm = make_vm()
    vm.next()
    make_vm.state["reqs"] = requirements()
    vm.recheck()
    assert vm.can_continue
    vm.next()
    assert vm.page == SetupPage.components


def test_skeletons_default_on_and_changes_needed_space(make_vm):
    vm = make_vm()
    both = vm.needed_bytes
    assert both == int((COMPONENT_SIZES["base"] + COMPONENT_SIZES["skeletons"]) * 1.1)
    vm.set_skeletons(False)
    assert vm.components == ["base"]
    assert vm.needed_bytes == int(COMPONENT_SIZES["base"] * 1.1)


def test_bad_folder_blocks_with_problem(make_vm, tmp_path):
    vm = make_vm(folder_check=lambda folder, needed: FolderCheck(False, "no_symlinks", 10**13, needed))
    walk_to_folder(vm)
    vm.choose_folder(tmp_path / "exfat")
    assert vm.folder_check.problem == "no_symlinks"
    assert not vm.can_continue
    vm.next()
    assert vm.page == SetupPage.folder
    assert FakeRunner.instances == []


def test_install_writes_pointer_then_runs_steps(make_vm, tmp_path):
    finished = []
    vm = make_vm()
    vm.finished.connect(lambda: finished.append(True))
    walk_to_folder(vm)
    vm.choose_folder(tmp_path / "Disco 2" / "M3D")
    vm.next()
    assert read_pointer(make_vm.env) == (tmp_path / "Disco 2" / "M3D").resolve()
    runner = FakeRunner.instances[0]
    assert runner.install_dir == tmp_path / "Disco 2" / "M3D"
    assert runner.components == ["base", "skeletons"]
    assert vm.page == SetupPage.done
    assert finished == [True]
    assert vm.log_lines == ["working"]


def test_step_failure_sets_error_and_retry_resumes(make_vm, tmp_path):
    FakeRunner.fail_with = StepFailed("no_space", "disk full", "weights")
    vm = make_vm()
    walk_to_folder(vm)
    vm.choose_folder(tmp_path / "m3d")
    vm.next()
    assert vm.page == SetupPage.install
    assert vm.error.code == "no_space" and vm.error.step_id == "weights"
    vm.retry()
    assert vm.error is None
    assert vm.page == SetupPage.done
    assert len(FakeRunner.instances) == 2


def test_cancel_returns_to_install_page_with_cancelled(make_vm, tmp_path):
    queued = []
    vm = make_vm(background=queued.append)
    vm.next()
    queued.pop(0)()  # the requirement check finishes; the install below never does
    vm.next()
    vm.next()
    vm.choose_folder(tmp_path / "m3d")
    vm.next()
    assert vm.page == SetupPage.install and len(queued) == 1
    vm.cancel()
    assert FakeRunner.instances[0].cancelled


def test_add_skeletons_runs_with_skeletons_added(make_vm, tmp_path):
    folder = tmp_path / "m3d"
    save_state(folder, InstallState(app_version="0.1.0", components=["base"]))
    vm = SetupViewModel.for_existing(
        folder, "add_skeletons", sources=Sources(tmp_path / "opt", tmp_path / "opt/bin/uv", "0.1.0"),
        env=make_vm.env, check=lambda: (requirements(), TOOLCHAIN), runner_factory=FakeRunner,
        recipe=fake_recipe, background=lambda work: work())
    assert FakeRunner.instances[0].components == ["base", "skeletons"]
    assert vm.page == SetupPage.done


def test_resume_with_failing_requirements_shows_requirements(make_vm, tmp_path):
    folder = tmp_path / "m3d"
    save_state(folder, InstallState(app_version="0.1.0", components=["base"]))
    vm = SetupViewModel.for_existing(
        folder, "resume", sources=Sources(tmp_path / "opt", tmp_path / "opt/bin/uv", "0.1.0"), env=make_vm.env,
        check=lambda: (requirements({RequirementId.driver}), None), runner_factory=FakeRunner,
        recipe=fake_recipe, background=lambda work: work())
    assert vm.page == SetupPage.requirements
    assert FakeRunner.instances == []


def test_missing_folder_mode_opens_missing_folder_page(make_vm, tmp_path):
    vm = SetupViewModel.for_existing(
        tmp_path / "unplugged", "missing_folder", sources=Sources(tmp_path, tmp_path / "uv", "0.1.0"),
        env=make_vm.env, check=lambda: (requirements(), TOOLCHAIN), runner_factory=FakeRunner,
        recipe=fake_recipe, background=lambda work: work())
    assert vm.page == SetupPage.missing_folder
    assert vm.folder == tmp_path / "unplugged"
    vm.start_over()
    assert vm.page == SetupPage.welcome


def test_retry_folder_resumes_once_the_disk_is_back(make_vm, tmp_path):
    folder = tmp_path / "unplugged"
    vm = SetupViewModel.for_existing(
        folder, "missing_folder", sources=Sources(tmp_path / "opt", tmp_path / "opt/bin/uv", "0.1.0"),
        env=make_vm.env, check=lambda: (requirements(), TOOLCHAIN), runner_factory=FakeRunner,
        recipe=fake_recipe, background=lambda work: work())
    vm.retry_folder()
    assert vm.page == SetupPage.missing_folder
    folder.mkdir()
    vm.retry_folder()
    assert vm.page == SetupPage.done
    assert FakeRunner.instances[0].install_dir == folder
