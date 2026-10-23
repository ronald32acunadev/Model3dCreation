"""Builds the application: services, view models, and the main window."""

import logging
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QWidget

import model3d_client
from model3d_client.i18n import apply_language
from model3d_client.paths import PACKAGE_DIR, REPO_ROOT, app_paths
from model3d_client.services.engine_api import EngineApi
from model3d_client.services.library import Library
from model3d_client.services.settings import load_settings
from model3d_client.services.supervisor import EngineSupervisor
from model3d_client.setup.checks import Toolchain
from model3d_client.setup.recipe import Sources, build_recipe
from model3d_client.setup.state import load_state, read_pointer
from model3d_client.viewmodels.generation import GenerationViewModel
from model3d_client.viewmodels.main import MainViewModel
from model3d_client.views.main_window import MainWindow
from model3d_client.views.scheme import prepare_webengine


INSTALLED_ROOT = Path("/opt/model3d")
# Startup only reads step ids and done checks, which do not depend on the toolchain.
PLACEHOLDER_TOOLCHAIN = Toolchain(Path("nvcc"), Path("/"), (13, 0), "", 1)
StartupMode = Literal["main", "setup", "resume", "update", "missing_folder", "development"]


def default_sources() -> Sources:
    """The app's own files: /opt/model3d when installed from the .deb, else this repository."""
    if PACKAGE_DIR.parent == INSTALLED_ROOT / "lib":
        return Sources(INSTALLED_ROOT, INSTALLED_ROOT / "bin/uv", model3d_client.__version__)
    return Sources(REPO_ROOT, Path(shutil.which("uv") or "uv"), model3d_client.__version__)


def startup_mode(env: Mapping[str, str], repo: Path) -> StartupMode:
    """Which window the app opens with: setup, the main window, or the wizard to resume, update, or find the install."""
    folder = read_pointer(env)
    if folder is None:
        return "development" if (repo / "engine/pyproject.toml").is_file() else "setup"
    if not folder.is_dir():
        return "missing_folder"
    state = load_state(folder)
    if state is None:
        return "resume"
    steps = build_recipe(default_sources(), folder, PLACEHOLDER_TOOLCHAIN, state.components)
    if any(step.id not in state.steps or not step.done_check(folder) for step in steps):
        return "resume"
    if state.app_version != model3d_client.__version__:
        return "update"
    return "main"


def build_app(
    argv: list[str],
    env: Mapping[str, str] | None = None,
    viewer_factory: Callable[[], QWidget] | None = None,
) -> tuple[QApplication, MainWindow]:
    """Create (or reuse) the QApplication and the main window; the engine is not started yet."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication.instance()
    if app is None:
        prepare_webengine()
        app = QApplication(argv)
    app.setApplicationName("Model3D")
    paths = app_paths(env)
    settings = load_settings(paths.settings_file, QLocale.system().name())
    apply_language(app, settings.language)
    api = EngineApi(parent=app)
    library = Library(paths.library)
    supervisor = EngineSupervisor(api, paths, parent=app)
    generation = GenerationViewModel(api, library, supervisor, settings, parent=app)
    vm = MainViewModel(paths, api, supervisor, library, generation, settings, parent=app)
    if viewer_factory is None:
        from model3d_client.views.viewer_widget import ViewerWidget

        viewer = ViewerWidget(paths.viewer_dir, paths.library)
    else:
        viewer = viewer_factory()
    return app, MainWindow(vm, viewer)


def build_setup(argv: list[str], env: Mapping[str, str], mode: str) -> tuple[QApplication, "SetupWizard"]:
    """Create (or reuse) the QApplication and the setup wizard for a startup mode other than main."""
    from model3d_client.viewmodels.setup import SetupViewModel, default_check
    from model3d_client.views.setup_wizard import SetupWizard

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication.instance()
    if app is None:
        prepare_webengine()
        app = QApplication(argv)
    app.setApplicationName("Model3D")
    apply_language(app, load_settings(app_paths(env).settings_file, QLocale.system().name()).language)
    kwargs = dict(sources=default_sources(), env=env, check=default_check(env), parent=app)
    folder = read_pointer(env)
    if mode == "setup" or folder is None:
        vm = SetupViewModel(**kwargs)
    else:
        # update and resume both redo what is pending; repair also rechecks every step's result.
        vm = SetupViewModel.for_existing(folder, "resume" if mode == "update" else mode, **kwargs)
    return app, SetupWizard(vm)
