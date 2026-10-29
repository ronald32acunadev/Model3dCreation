"""User settings, stored as JSON in the data folder."""

import logging
import os
from pathlib import Path
from typing import Literal

from model3d_contracts.jobs import Quality
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class Settings(BaseModel):
    language: Literal["en", "es"] = "en"
    default_texture: bool = True
    default_quality: Quality = Quality.final
    export_dir: Path = Field(default_factory=Path.home)


def load_settings(path: Path, system_locale: str) -> Settings:
    """Saved settings, or defaults on first run (Spanish when the system locale is Spanish)."""
    try:
        return Settings.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return Settings(language="es" if system_locale.lower().startswith("es") else "en")
    except (OSError, ValidationError) as error:
        logger.warning("Ignoring unreadable settings %s: %s", path, error)
        return Settings()


def save_settings(path: Path, settings: Settings) -> None:
    """Write the settings atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(settings.model_dump_json(indent=2))
    os.replace(tmp, path)
