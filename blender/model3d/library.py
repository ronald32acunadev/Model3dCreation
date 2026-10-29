"""The desktop app's library (data/library), read and written without the app's code.

The format is the app's `Entry` (client/src/model3d_client/services/library.py): one folder per
entry named <YYYYmmddTHHMMSS>-<6 hex>, holding entry.json, input.<ext>, and model.glb.
client/tests/test_addon_library_compat.py checks that the app reads what this module writes.
"""

import json
import os
import secrets
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

ENTRY_FILE = "entry.json"
MODEL_FILE = "model.glb"


def default_root(env: Mapping[str, str]) -> Path | None:
    """The desktop app's library, found through its install pointer, or None when there is no usable pointer."""
    if env.get("XDG_CONFIG_HOME"):
        config_dir = Path(env["XDG_CONFIG_HOME"])
    elif env.get("HOME"):
        config_dir = Path(env["HOME"]) / ".config"
    else:
        return None
    try:
        data = json.loads((config_dir / "model3d" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    install_dir = data.get("install_dir") if isinstance(data, dict) else None
    if not isinstance(install_dir, str) or not install_dir:
        return None
    return Path(install_dir) / "data" / "library"


def model_path(root: Path, entry_id: str) -> Path:
    return root / entry_id / MODEL_FILE


def list_entries(root: Path) -> list[dict]:
    """Readable entries, newest first; a broken or half-written entry is skipped."""
    if not root.is_dir():
        return []
    found = []
    for folder in root.iterdir():
        try:
            entry = json.loads((folder / ENTRY_FILE).read_text())
            if isinstance(entry, dict) and entry.get("entry_id") == folder.name:
                found.append(entry)
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda entry: (entry.get("created_at", ""), entry["entry_id"]), reverse=True)


def create_entry(root: Path, image: Path, options: dict, name: str | None = None,
                 source_entry_id: str | None = None) -> dict:
    """A new running entry holding a copy of the image."""
    now = datetime.now(UTC)
    entry_id = f"{now:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
    input_file = "input" + image.suffix.lower()
    (root / entry_id).mkdir(parents=True)
    shutil.copyfile(image, root / entry_id / input_file)
    entry = {
        "entry_id": entry_id,
        "name": name or image.stem,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "options": options,
        "seed": None,
        "job_id": None,
        "outcome": "running",
        "error": None,
        "stats": None,
        "input_file": input_file,
        "source_entry_id": source_entry_id,
    }
    update_entry(root, entry)
    return entry


def update_entry(root: Path, entry: dict) -> None:
    """Write entry.json through a temporary file, so readers never see half of it."""
    path = root / entry["entry_id"] / ENTRY_FILE
    tmp = path.with_name(ENTRY_FILE + ".tmp")
    tmp.write_text(json.dumps(entry, indent=2))
    os.replace(tmp, path)
