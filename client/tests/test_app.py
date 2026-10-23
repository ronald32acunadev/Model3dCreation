from datetime import UTC, datetime
from pathlib import Path

import pytest

import model3d_client
from fakes import StubViewer
from model3d_client.app import PLACEHOLDER_TOOLCHAIN, build_app, default_sources, startup_mode
from model3d_client.paths import REPO_ROOT
from model3d_client.services.supervisor import EngineState
from model3d_client.setup.recipe import build_recipe
from model3d_client.setup.state import InstallState, StepRecord, save_state, write_pointer


def test_build_app_wires_window_without_starting_engine(qtbot, tmp_path):
    app, window = build_app([], env={"MODEL3D_HOME": str(tmp_path)}, viewer_factory=StubViewer)
    window.ask_close_anyway = lambda: True
    qtbot.addWidget(window)
    assert window.vm.paths.data == tmp_path
    assert window.vm.supervisor.state == EngineState.stopped
    assert window.windowTitle() == "Model3D"
    assert window.vm.library.root == tmp_path / "library"



@pytest.fixture
def installed(tmp_path):
    """An env whose pointer names a folder; returns (env, folder)."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    folder = tmp_path / "Disco 2" / "M3D"
    folder.mkdir(parents=True)
    write_pointer(env, folder)
    return env, folder.resolve()


def finish_install(folder: Path, components: list[str], version: str) -> None:
    """Record every recipe step and create what each step's done check looks for."""
    steps = build_recipe(default_sources(), folder, PLACEHOLDER_TOOLCHAIN, components)
    for path in ("python/cpython-3.11.17-linux-x86_64-gnu", "engine", "contracts", "vendor/Hunyuan3D-2.1/hy3dshape",
                 "vendor/Hunyuan3D-2.1/hy3dpaint/ckpt", "engine/.venv/lib/python3.11/site-packages/custom_rasterizer",
                 "vendor/Hunyuan3D-2.1/hy3dpaint/DifferentiableRenderer",
                 "data/hf/hub/models--tencent--Hunyuan3D-2.1/snapshots/s/hunyuan3d-dit-v2-1",
                 "data/hf/hub/models--facebook--dinov2-giant/snapshots/s", "data/u2net", "data/logs"):
        (folder / path).mkdir(parents=True, exist_ok=True)
    for path in ("engine/pyproject.toml", "contracts/pyproject.toml",
                 "vendor/Hunyuan3D-2.1/hy3dpaint/ckpt/RealESRGAN_x4plus.pth",
                 "vendor/Hunyuan3D-2.1/hy3dpaint/DifferentiableRenderer/mesh_inpaint_processor.cpython-311.so",
                 "data/hf/hub/models--facebook--dinov2-giant/snapshots/s/model.safetensors",
                 "data/u2net/u2net.onnx", "data/logs/verify.ok"):
        (folder / path).write_text("x")
    record = StepRecord(fingerprint="f", finished_at=datetime.now(UTC))
    save_state(folder, InstallState(app_version=version, components=components,
                                    steps={step.id: record for step in steps}))


def test_startup_mode_development_from_a_checkout(tmp_path):
    assert startup_mode({"XDG_CONFIG_HOME": str(tmp_path)}, REPO_ROOT) == "development"


def test_startup_mode_setup_when_installed_without_pointer(tmp_path):
    assert startup_mode({"XDG_CONFIG_HOME": str(tmp_path)}, tmp_path / "opt/model3d/lib") == "setup"


def test_startup_mode_missing_folder(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    write_pointer(env, tmp_path / "unplugged")
    assert startup_mode(env, REPO_ROOT) == "missing_folder"


def test_startup_mode_resume_when_a_step_is_missing(installed):
    env, folder = installed
    assert startup_mode(env, REPO_ROOT) == "resume"  # no install.json yet
    finish_install(folder, ["base"], model3d_client.__version__)
    (folder / "data/logs/verify.ok").unlink()
    assert startup_mode(env, REPO_ROOT) == "resume"


def test_startup_mode_update_when_the_app_version_changed(installed):
    env, folder = installed
    finish_install(folder, ["base"], "0.0.9")
    assert startup_mode(env, REPO_ROOT) == "update"


def test_startup_mode_main_when_installed_and_current(installed):
    env, folder = installed
    finish_install(folder, ["base"], model3d_client.__version__)
    assert startup_mode(env, REPO_ROOT) == "main"


def test_version_matches_the_project():
    pyproject = (REPO_ROOT / "client/pyproject.toml").read_text()
    assert f'version = "{model3d_client.__version__}"' in pyproject


def test_development_sources_point_at_the_repository():
    sources = default_sources()
    assert sources.app_root == REPO_ROOT
    assert sources.app_version == model3d_client.__version__


def test_build_setup_resumes_an_existing_install(qtbot, installed):
    from model3d_client.app import build_setup
    from model3d_client.viewmodels.setup import SetupPage

    env, folder = installed
    _app, wizard = build_setup([], env, "resume")
    qtbot.addWidget(wizard)
    assert wizard.vm.folder == folder
    assert wizard.vm.page in (SetupPage.requirements, SetupPage.install)


def test_build_setup_first_run_starts_on_welcome(qtbot, tmp_path):
    from model3d_client.app import build_setup
    from model3d_client.viewmodels.setup import SetupPage

    _app, wizard = build_setup([], {"XDG_CONFIG_HOME": str(tmp_path)}, "setup")
    qtbot.addWidget(wizard)
    assert wizard.vm.page == SetupPage.welcome
