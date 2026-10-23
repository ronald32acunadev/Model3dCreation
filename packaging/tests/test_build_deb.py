"""Build the .deb once and inspect it with dpkg-deb; needs dpkg-deb and zip, no root, no network."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "packaging/deb/build.sh"
VERSION = next(line.split('"')[1] for line in (REPO / "client/pyproject.toml").read_text().splitlines()
               if line.startswith("version ="))


@pytest.fixture(scope="module")
def deb(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    subprocess.run([str(BUILD), "--out", str(out)], check=True, env={**os.environ, "UV_BINARY": uv})
    return out / f"model3d_{VERSION}_amd64.deb"


def field(deb: Path, name: str) -> str:
    return subprocess.run(["dpkg-deb", "--field", str(deb), name], check=True, capture_output=True,
                          text=True).stdout.strip()


def contents(deb: Path) -> list[str]:
    listing = subprocess.run(["dpkg-deb", "--contents", str(deb)], check=True, capture_output=True,
                             text=True).stdout
    return [line.split()[-1].removeprefix(".") for line in listing.splitlines()]


def test_build_produces_deb(deb):
    assert deb.is_file()


def test_control_fields_and_depends(deb):
    assert field(deb, "Package") == "model3d"
    assert field(deb, "Version") == VERSION
    assert field(deb, "Architecture") == "amd64"
    depends = {item.strip() for item in field(deb, "Depends").split(",")}
    for package in ("python3", "python3-pydantic", "build-essential", "patch", "python3-pyside6.qtwidgets",
                    "python3-pyside6.qtwebenginewidgets", "python3-pyside6.qtwebsockets", "python3-pyside6.qtnetwork"):
        assert package in depends


def test_contents_has_app_engine_uv_addon_and_launcher(deb):
    paths = contents(deb)
    for path in ("/opt/model3d/lib/model3d_client/__main__.py",
                 "/opt/model3d/lib/model3d_client/viewer/vendor/three.module.js",
                 "/opt/model3d/lib/model3d_client/i18n/model3d_es.qm",
                 "/opt/model3d/lib/model3d_contracts/jobs.py",
                 "/opt/model3d/engine/pyproject.toml", "/opt/model3d/engine/uv.lock",
                 "/opt/model3d/engine/patches/hunyuan3d-paint-device.patch",
                 "/opt/model3d/engine/src/model3d_engine/__main__.py",
                 "/opt/model3d/contracts/pyproject.toml", "/opt/model3d/scripts/verify_gpus.py",
                 "/opt/model3d/bin/uv", "/opt/model3d/blender/model3d.zip", "/usr/bin/model3d",
                 "/usr/share/applications/model3d.desktop",
                 "/usr/share/icons/hicolor/scalable/apps/model3d.svg"):
        assert path in paths, path


def test_contents_has_no_tests_venv_or_pycache(deb):
    for path in contents(deb):
        assert "/tests/" not in path and "/.venv" not in path and "__pycache__" not in path, path


def test_desktop_entry_execs_usr_bin_model3d(deb, tmp_path):
    subprocess.run(["dpkg-deb", "--extract", str(deb), str(tmp_path)], check=True)
    entry = (tmp_path / "usr/share/applications/model3d.desktop").read_text()
    assert "Exec=/usr/bin/model3d" in entry
    assert "Icon=model3d" in entry
    launcher = tmp_path / "usr/bin/model3d"
    assert os.access(launcher, os.X_OK)
    assert "PYTHONPATH=/opt/model3d/lib" in launcher.read_text()


def test_addon_zip_has_the_manifest_at_its_root(deb, tmp_path):
    subprocess.run(["dpkg-deb", "--extract", str(deb), str(tmp_path)], check=True)
    names = zipfile.ZipFile(tmp_path / "opt/model3d/blender/model3d.zip").namelist()
    assert "blender_manifest.toml" in names
    assert "__init__.py" in names
    assert not any("__pycache__" in name for name in names)


def test_postrm_names_the_kept_folder(deb, tmp_path):
    subprocess.run(["dpkg-deb", "--control", str(deb), str(tmp_path / "control")], check=True)
    script = (tmp_path / "control/postrm").read_text()
    assert "config.json" in script and "remove" in script
