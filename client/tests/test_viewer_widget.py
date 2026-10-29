import shutil
from pathlib import Path

import pytest

from model3d_client.paths import REPO_ROOT, app_paths
from model3d_client.views.viewer_widget import ViewerWidget

CUBE = Path(__file__).parent / "assets" / "cube.glb"


@pytest.mark.webengine
def test_loads_glb_without_error(qtbot, tmp_path):
    library = tmp_path / "library"
    (library / "E1").mkdir(parents=True)
    shutil.copyfile(CUBE, library / "E1" / "model.glb")
    viewer = ViewerWidget(app_paths({}, repo=REPO_ROOT).viewer_dir, library)
    qtbot.addWidget(viewer)
    viewer.resize(400, 300)
    viewer.show()
    errors = []
    viewer.load_failed.connect(errors.append)
    viewer.show_model(library / "E1" / "model.glb")
    viewer.set_textured(False)
    viewer.reset_camera()

    loaded = []

    def poll():
        viewer.is_loaded(loaded.append)
        return bool(loaded and loaded[-1])

    qtbot.waitUntil(lambda: poll() or bool(errors), timeout=20000)
    assert errors == []
    assert loaded[-1] is True
