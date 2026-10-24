"""Where the app finds the repository, the engine, and its runtime data."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from model3d_client.setup.state import read_pointer

PACKAGE_DIR = Path(__file__).resolve().parent
# client/src/model3d_client/paths.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AppPaths:
    repo: Path
    data: Path

    @property
    def library(self) -> Path:
        return self.data / "library"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"

    @property
    def engine_log(self) -> Path:
        return self.data / "logs" / "engine.log"

    @property
    def hf(self) -> Path:
        return self.data / "hf"

    @property
    def engine_python(self) -> Path:
        return self.repo / "engine/.venv/bin/python"

    @property
    def hunyuan(self) -> Path:
        return self.repo / "vendor/Hunyuan3D-2.1"

    @property
    def viewer_dir(self) -> Path:
        return PACKAGE_DIR / "viewer"


def app_paths(env: Mapping[str, str] | None = None, repo: Path = REPO_ROOT) -> AppPaths:
    """Paths for this run: the installed folder when the setup pointer names one, else the repository.

    MODEL3D_HOME moves the data folder, as it does for the engine.
    """
    env = os.environ if env is None else env
    repo = read_pointer(env) or repo
    home = env.get("MODEL3D_HOME")
    return AppPaths(repo=repo, data=Path(home).expanduser() if home else repo / "data")
