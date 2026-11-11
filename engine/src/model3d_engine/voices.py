"""Saved voices, one folder per voice under the voices root, shared by the app and the engine."""

import logging
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)
VOICE_FILE = "voice.json"
SAMPLE_FILE = "sample.wav"
VOICE_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]*")
Language = Literal["es", "en"]


class VoiceKind(str, Enum):
    designed = "designed"
    recorded = "recorded"


class Voice(BaseModel):
    voice_id: str
    name: str
    kind: VoiceKind
    language: Language
    created_at: datetime
    transcript: str  # the text spoken in the sample; cloning uses it later


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Name must not be empty")
    return cleaned


class VoiceStore:
    """Voices live in separate folders, so one unreadable voice never hides the others."""

    def __init__(self, root: Path):
        self.root = root

    def folder(self, voice_id: str) -> Path:
        return self.root / voice_id

    def sample_path(self, voice_id: str) -> Path:
        return self.folder(voice_id) / SAMPLE_FILE

    def voices(self) -> list[Voice]:
        """All readable voices, newest first."""
        if not self.root.is_dir():
            return []
        found = []
        for folder in self.root.iterdir():
            if (folder / VOICE_FILE).is_file():
                try:
                    found.append(self._read(folder.name))
                except (OSError, ValidationError) as error:
                    logger.warning("Skipping unreadable voice %s: %s", folder, error)
        return sorted(found, key=lambda voice: (voice.created_at, voice.voice_id), reverse=True)

    def get(self, voice_id: str) -> Voice | None:
        if VOICE_ID.fullmatch(voice_id) is None or not (self.folder(voice_id) / VOICE_FILE).is_file():
            return None
        try:
            return self._read(voice_id)
        except (OSError, ValidationError):
            return None

    def create(self, name: str, kind: VoiceKind, language: str, transcript: str, sample: bytes) -> Voice:
        name = _clean_name(name)
        now = datetime.now(UTC)
        voice_id = f"{now:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
        voice = Voice(
            voice_id=voice_id,
            name=name,
            kind=kind,
            language=language,
            created_at=now,
            transcript=transcript,
        )
        folder = self.folder(voice_id)
        try:
            folder.mkdir(parents=True)
            self.sample_path(voice_id).write_bytes(sample)
            self._write(voice)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return voice

    def rename(self, voice_id: str, name: str) -> Voice:
        name = _clean_name(name)
        voice = self.get(voice_id)
        if voice is None:
            raise KeyError(voice_id)
        renamed = voice.model_copy(update={"name": name})
        self._write(renamed)
        return renamed

    def delete(self, voice_id: str) -> None:
        shutil.rmtree(self.folder(voice_id))

    def _write(self, voice: Voice) -> None:
        path = self.folder(voice.voice_id) / VOICE_FILE
        tmp = path.with_name(VOICE_FILE + ".tmp")
        tmp.write_text(voice.model_dump_json(indent=2))
        os.replace(tmp, path)

    def _read(self, voice_id: str) -> Voice:
        return Voice.model_validate_json((self.folder(voice_id) / VOICE_FILE).read_bytes())
