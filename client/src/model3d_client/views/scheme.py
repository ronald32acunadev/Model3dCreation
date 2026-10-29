"""The model3d:// URL scheme: serves the viewer page and library models to QtWebEngine.

Chromium's fetch() refuses file:// URLs, and GLTFLoader loads through fetch(), so the
viewer and the models it shows come from a custom scheme limited to a few folders.
"""

import mimetypes
import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QCoreApplication, QFile, QIODevice, Qt, QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler

SCHEME = b"model3d"
MIME_TYPES = {".html": "text/html", ".js": "text/javascript", ".glb": "model/gltf-binary", ".css": "text/css"}


def register_scheme() -> None:
    """Declare the scheme; must run before the QApplication is created."""
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def prepare_webengine() -> None:
    """Everything QtWebEngine needs before the QApplication exists.

    Chromium blocklists WebGL on this NVIDIA + Wayland setup (it cannot identify the driver),
    which leaves the viewer black; --ignore-gpu-blocklist lets WebGL run. A flag list the
    user set in QTWEBENGINE_CHROMIUM_FLAGS is extended, never replaced.
    """
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--ignore-gpu-blocklist" not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --ignore-gpu-blocklist").strip()
    from PySide6 import QtWebEngineWidgets  # noqa: F401  # must be imported before the QApplication

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    register_scheme()


class AssetSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves model3d://<root>/<path> from the folder registered for <root>, and nothing outside it."""

    def __init__(self, roots: dict[str, Path], parent=None):
        super().__init__(parent)
        self._roots = {name: path.resolve() for name, path in roots.items()}

    def resolve(self, url: QUrl) -> Path | None:
        root = self._roots.get(url.host())
        if root is None:
            return None
        relative = url.path(QUrl.ComponentFormattingOption.FullyDecoded).lstrip("/")
        target = (root / relative).resolve()  # follows symlinks and ".." before the containment check
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        path = self.resolve(job.requestUrl())
        if path is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        file = QFile(str(path), job)  # the job owns the file and closes it when done
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        mime = MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        job.reply(QByteArray(mime.encode()), file)
