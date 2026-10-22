"""The 3D viewer: a QWebEngineView showing viewer/index.html through the model3d:// scheme."""

import json
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from model3d_client.views.scheme import SCHEME, AssetSchemeHandler

ERROR_PREFIX = "model3d-viewer-error: "


class _ViewerPage(QWebEnginePage):
    viewer_error = Signal(str)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith(ERROR_PREFIX):
            self.viewer_error.emit(message[len(ERROR_PREFIX):])


class ViewerWidget(QWebEngineView):
    load_failed = Signal(str)

    def __init__(self, viewer_dir: Path, library_dir: Path, parent=None):
        super().__init__(parent)
        self._library_dir = library_dir.resolve()
        profile = QWebEngineProfile.defaultProfile()
        # One handler per profile; keep a reference so Python does not free it.
        self._handler = AssetSchemeHandler({"viewer": viewer_dir, "library": library_dir}, self)
        profile.removeUrlScheme(SCHEME)
        profile.installUrlSchemeHandler(SCHEME, self._handler)
        page = _ViewerPage(profile, self)
        page.viewer_error.connect(self.load_failed)
        self.setPage(page)
        self._page_ready = False
        self._pending: list[str] = []
        self.loadFinished.connect(self._on_load_finished)
        self.setUrl(QUrl("model3d://viewer/index.html"))

    def show_model(self, path: Path) -> None:
        """Show a GLB from the library folder."""
        relative = path.resolve().relative_to(self._library_dir).as_posix()
        self._run(f"window.viewer.load({json.dumps('model3d://library/' + relative)})")

    def clear(self) -> None:
        self._run("window.viewer.clear()")

    def set_textured(self, textured: bool) -> None:
        self._run(f"window.viewer.setTextured({json.dumps(textured)})")

    def reset_camera(self) -> None:
        self._run("window.viewer.resetCamera()")

    def is_loaded(self, callback) -> None:
        """Ask the page whether the current model is shown; callback gets a bool."""
        self.page().runJavaScript("!!(window.viewer && window.viewer.loaded)", 0, lambda value: callback(bool(value)))

    def _run(self, script: str) -> None:
        if self._page_ready:
            # The module script may still be starting: wait for window.viewer.
            self.page().runJavaScript(f"(function run(){{ if (window.viewer) {{ {script}; }} else setTimeout(run, 50); }})()")
        else:
            self._pending.append(script)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self.load_failed.emit("The viewer page did not load")
            return
        self._page_ready = True
        pending, self._pending = self._pending, []
        for script in pending:
            self._run(script)
