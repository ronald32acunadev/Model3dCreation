import pytest
from PySide6.QtCore import QUrl

from model3d_client.views.scheme import AssetSchemeHandler


@pytest.fixture
def handler(tmp_path):
    (tmp_path / "viewer").mkdir()
    (tmp_path / "viewer" / "index.html").write_text("<html></html>")
    (tmp_path / "lib" / "E1").mkdir(parents=True)
    (tmp_path / "lib" / "E1" / "model.glb").write_bytes(b"glTF")
    (tmp_path / "secret").write_text("x")
    return AssetSchemeHandler({"viewer": tmp_path / "viewer", "library": tmp_path / "lib"})


def test_resolves_viewer_file(handler, tmp_path):
    assert handler.resolve(QUrl("model3d://viewer/index.html")) == (tmp_path / "viewer/index.html").resolve()
    assert handler.resolve(QUrl("model3d://library/E1/model.glb")) == (tmp_path / "lib/E1/model.glb").resolve()


def test_unknown_root_is_none(handler):
    assert handler.resolve(QUrl("model3d://elsewhere/index.html")) is None


def test_missing_file_is_none(handler):
    assert handler.resolve(QUrl("model3d://viewer/nope.js")) is None
    assert handler.resolve(QUrl("model3d://library/E1")) is None  # a folder, not a file


def test_rejects_path_traversal(handler):
    assert handler.resolve(QUrl("model3d://library/../secret")) is None
    assert handler.resolve(QUrl("model3d://library/%2e%2e/secret")) is None
    assert handler.resolve(QUrl("model3d://library/E1/%2E%2E/%2E%2E/secret")) is None
    assert handler.resolve(QUrl("model3d://library/..%2Fsecret")) is None


def test_rejects_symlink_out_of_root(handler, tmp_path):
    (tmp_path / "lib" / "link").symlink_to(tmp_path / "secret")
    assert handler.resolve(QUrl("model3d://library/link")) is None
