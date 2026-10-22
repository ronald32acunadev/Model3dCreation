"""Builds the application: services, view models, and the main window."""

import logging
from collections.abc import Callable, Mapping

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QWidget

from model3d_client.i18n import apply_language
from model3d_client.paths import app_paths
from model3d_client.services.engine_api import EngineApi
from model3d_client.services.library import Library
from model3d_client.services.settings import load_settings
from model3d_client.services.supervisor import EngineSupervisor
from model3d_client.viewmodels.generation import GenerationViewModel
from model3d_client.viewmodels.main import MainViewModel
from model3d_client.views.main_window import MainWindow
from model3d_client.views.scheme import prepare_webengine


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
