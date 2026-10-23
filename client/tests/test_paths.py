from pathlib import Path

import model3d_client
from model3d_client.paths import REPO_ROOT, app_paths
from model3d_client.setup.state import write_pointer


def test_default_data_is_repo_data(tmp_path):
    p = app_paths({}, repo=tmp_path)
    assert p.data == tmp_path / "data"
    assert p.library == tmp_path / "data/library"
    assert p.settings_file == tmp_path / "data/settings.json"
    assert p.engine_log == tmp_path / "data/logs/engine.log"
    assert p.hf == tmp_path / "data/hf"
    assert p.engine_python == tmp_path / "engine/.venv/bin/python"
    assert p.hunyuan == tmp_path / "vendor/Hunyuan3D-2.1"


def test_model3d_home_overrides_data(tmp_path):
    assert app_paths({"MODEL3D_HOME": str(tmp_path / "x")}, repo=tmp_path).data == tmp_path / "x"


def test_repo_root_is_this_repository():
    assert (REPO_ROOT / "client/pyproject.toml").is_file()
    assert (REPO_ROOT / "engine/pyproject.toml").is_file()


def test_pointer_moves_repo_and_data(tmp_path):
    install = tmp_path / "Disco 2" / "M3D"
    install.mkdir(parents=True)
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    write_pointer(env, install)
    p = app_paths(env, repo=tmp_path / "repo")
    assert p.repo == install.resolve()
    assert p.data == install.resolve() / "data"
    assert p.engine_python == install.resolve() / "engine/.venv/bin/python"


def test_model3d_home_overrides_pointer_data(tmp_path):
    install = tmp_path / "Disco 2" / "M3D"
    install.mkdir(parents=True)
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "MODEL3D_HOME": str(tmp_path / "x")}
    write_pointer(env, install)
    p = app_paths(env, repo=tmp_path / "repo")
    assert p.repo == install.resolve()
    assert p.data == tmp_path / "x"


def test_no_pointer_keeps_repository_paths(tmp_path):
    p = app_paths({"XDG_CONFIG_HOME": str(tmp_path / "empty")}, repo=tmp_path)
    assert p.repo == tmp_path
    assert p.data == tmp_path / "data"


def test_viewer_dir_is_inside_the_package(tmp_path):
    assert app_paths({}, repo=tmp_path).viewer_dir == Path(model3d_client.__file__).parent / "viewer"
