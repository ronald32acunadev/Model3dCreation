"""TRELLIS.2 worker config: write_config must point the ckpts link at the current snapshot.

trellis_worker.py runs in TRELLIS's own venv and redirects fd 1 at import time, so importing it
here would hijack pytest's stdout. Only write_config is extracted from the source and executed.
"""

import ast
import json
import os
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "src" / "model3d_engine" / "stages" / "trellis_worker.py"


def load_write_config():
    tree = ast.parse(WORKER.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write_config")
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {"json": json, "os": os, "Path": Path}
    exec(compile(module, str(WORKER), "exec"), namespace)
    return namespace["write_config"]


write_config = load_write_config()


@pytest.fixture
def snapshot(tmp_path):
    folder = tmp_path / "snapshot"
    (folder / "ckpts").mkdir(parents=True)
    (folder / "ckpts" / "shape.json").write_text("{}")
    (folder / "pipeline.json").write_text(json.dumps({"args": {}}))
    return folder


def test_fresh_config_dir_gets_low_vram_and_link(tmp_path, snapshot):
    config_dir = tmp_path / "config"

    write_config(snapshot, config_dir)

    args = json.loads((config_dir / "pipeline.json").read_text())
    assert args["args"]["low_vram"] is True
    link = config_dir / "ckpts"
    assert link.is_symlink()
    assert os.readlink(link) == str(snapshot / "ckpts")


def test_stale_link_to_missing_path_is_replaced(tmp_path, snapshot):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "ckpts").symlink_to(tmp_path / "old" / "ckpts")

    write_config(snapshot, config_dir)

    link = config_dir / "ckpts"
    assert os.readlink(link) == str(snapshot / "ckpts")
    assert (link / "shape.json").read_text() == "{}"


def test_correct_link_is_left_unchanged(tmp_path, snapshot):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    link = config_dir / "ckpts"
    link.symlink_to(snapshot / "ckpts")
    before = os.lstat(link).st_ino

    write_config(snapshot, config_dir)

    assert os.readlink(link) == str(snapshot / "ckpts")
    assert os.lstat(link).st_ino == before


def test_real_ckpts_directory_is_left_alone(tmp_path, snapshot):
    config_dir = tmp_path / "config"
    (config_dir / "ckpts").mkdir(parents=True)
    marker = config_dir / "ckpts" / "local.json"
    marker.write_text("{}")

    write_config(snapshot, config_dir)

    assert not (config_dir / "ckpts").is_symlink()
    assert marker.read_text() == "{}"
