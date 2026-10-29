"""Install state: the config pointer to the install folder and the folder's install.json."""

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

CONFIG_NAME = "config.json"
STATE_FILE = "install.json"


def config_file(env: Mapping[str, str]) -> Path | None:
    """The pointer file; read from the mapping only, so tests never see the real one."""
    if xdg := env.get("XDG_CONFIG_HOME"):
        return Path(xdg) / "model3d" / CONFIG_NAME
    if home := env.get("HOME"):
        return Path(home) / ".config" / "model3d" / CONFIG_NAME
    return None


def read_pointer(env: Mapping[str, str]) -> Path | None:
    """The install folder the pointer names, or None when there is no usable pointer."""
    path = config_file(env)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    install_dir = data.get("install_dir") if isinstance(data, dict) else None
    return Path(install_dir) if isinstance(install_dir, str) and install_dir else None


def write_pointer(env: Mapping[str, str], install_dir: Path) -> None:
    """Point the app at an install folder, atomically."""
    path = config_file(env)
    if path is None:
        raise RuntimeError("No HOME or XDG_CONFIG_HOME to store the install pointer")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"install_dir": str(install_dir.resolve())}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


class StepRecord(BaseModel):
    fingerprint: str
    finished_at: datetime


class InstallState(BaseModel):
    app_version: str
    components: list[str]  # "base", "skeletons"
    steps: dict[str, StepRecord] = Field(default_factory=dict)


def load_state(install_dir: Path) -> InstallState | None:
    """The folder's install.json, or None when it is missing or unreadable."""
    try:
        return InstallState.model_validate_json((install_dir / STATE_FILE).read_bytes())
    except (OSError, ValidationError):
        return None


def save_state(install_dir: Path, state: InstallState) -> None:
    """Write install.json atomically."""
    install_dir.mkdir(parents=True, exist_ok=True)
    path = install_dir / STATE_FILE
    tmp = path.with_name(STATE_FILE + ".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
