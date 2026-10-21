from model3d_client.paths import REPO_ROOT, app_paths


def test_default_data_is_repo_data(tmp_path):
    p = app_paths({}, repo=tmp_path)
    assert p.data == tmp_path / "data"
    assert p.library == tmp_path / "data/library"
    assert p.settings_file == tmp_path / "data/settings.json"
    assert p.engine_log == tmp_path / "data/logs/engine.log"
    assert p.hf == tmp_path / "data/hf"
    assert p.engine_python == tmp_path / "engine/.venv/bin/python"
    assert p.hunyuan == tmp_path / "vendor/Hunyuan3D-2.1"
    assert p.viewer_dir == tmp_path / "client/src/model3d_client/viewer"


def test_model3d_home_overrides_data(tmp_path):
    assert app_paths({"MODEL3D_HOME": str(tmp_path / "x")}, repo=tmp_path).data == tmp_path / "x"


def test_repo_root_is_this_repository():
    assert (REPO_ROOT / "client/pyproject.toml").is_file()
    assert (REPO_ROOT / "engine/pyproject.toml").is_file()
