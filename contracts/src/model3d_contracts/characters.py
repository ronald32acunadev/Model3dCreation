"""Characters: a root library entry, every entry derived from it, and the data stored with them."""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from model3d_contracts.library import Entry, Library

logger = logging.getLogger(__name__)
CHARACTER_FILE = "character.json"
PHRASES_DIR = "phrases"
_PHRASE_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{0,63}")


class CharacterData(BaseModel):
    """What a character stores beside its versions; US-09 adds fields."""

    voice_id: str | None = None


class Phrase(BaseModel):
    """A finished phrase a character said; its id is the speech job's id."""

    phrase_id: str
    text: str
    language: Literal["es", "en"]
    created_at: datetime


class Character(BaseModel):
    character_id: str
    name: str
    created_at: datetime
    input_file: str
    versions: list[str]
    voice_id: str | None
    phrase_count: int


def _root_id(entry: Entry, by_id: dict[str, Entry]) -> str:
    """The id of the root above an entry. A cycle makes the entry its own root; so does a missing source."""
    seen = {entry.entry_id}
    current = entry
    while current.source_entry_id is not None:
        source = by_id.get(current.source_entry_id)
        if source is None:
            break
        if source.entry_id in seen:
            return entry.entry_id
        seen.add(source.entry_id)
        current = source
    return current.entry_id


class Characters:
    """Characters of a library. Reading never writes, so libraries without character data stay untouched."""

    def __init__(self, library: Library):
        self.library = library

    def characters(self) -> list[Character]:
        groups = sorted(
            self._groups().values(),
            key=lambda group: (group[0].created_at, group[0].entry_id),
            reverse=True,
        )
        return [self._character(root, derived) for root, derived in groups]

    def get(self, character_id: str) -> Character | None:
        groups = self._groups()
        if character_id not in groups:
            return None
        return self._character(*groups[character_id])

    def rename(self, character_id: str, name: str) -> Character:
        root, _ = self._group(character_id)
        self.library.rename(root.entry_id, name)
        return self._character(*self._group(character_id))

    def data(self, character_id: str) -> CharacterData:
        root, _ = self._group(character_id)
        return self._read_data(root.entry_id)

    def save_data(self, character_id: str, data: CharacterData) -> None:
        root, _ = self._group(character_id)
        path = self._data_path(root.entry_id)
        if data == CharacterData():
            path.unlink(missing_ok=True)
            return
        tmp = path.with_name(CHARACTER_FILE + ".tmp")
        tmp.write_text(data.model_dump_json(indent=2))
        os.replace(tmp, path)

    def clear_voice(self, voice_id: str) -> list[str]:
        """Unlink a voice from every character that uses it; returns those characters' ids."""
        cleared = []
        for character in self.characters():
            if character.voice_id == voice_id:
                data = self._read_data(character.character_id)
                self.save_data(character.character_id, data.model_copy(update={"voice_id": None}))
                cleared.append(character.character_id)
        return cleared

    def phrases(self, character_id: str) -> list[Phrase]:
        """The character's phrases, newest first; unreadable ones are skipped."""
        root, _ = self._group(character_id)
        folder = self.library.folder(root.entry_id) / PHRASES_DIR
        if not folder.is_dir():
            return []
        found = []
        for path in folder.glob("*.json"):
            try:
                found.append(Phrase.model_validate_json(path.read_bytes()))
            except (OSError, ValidationError) as error:
                logger.warning("Ignoring unreadable phrase %s: %s", path, error)
        return sorted(found, key=lambda phrase: (phrase.created_at, phrase.phrase_id), reverse=True)

    def phrase_audio(self, character_id: str, phrase_id: str) -> Path | None:
        """The WAV of a phrase, or None when the character has no such phrase."""
        root, _ = self._group(character_id)
        if _PHRASE_ID.fullmatch(phrase_id) is None:
            return None
        folder = self.library.folder(root.entry_id) / PHRASES_DIR
        if not (folder / f"{phrase_id}.json").is_file():
            return None
        audio = folder / f"{phrase_id}.wav"
        return audio if audio.is_file() else None

    def add_phrase(self, character_id: str, phrase: Phrase, audio: Path) -> None:
        """Keep a phrase: the WAV is copied first and the JSON written last, so a counted phrase always has audio."""
        if _PHRASE_ID.fullmatch(phrase.phrase_id) is None:
            raise ValueError(f"Invalid phrase id {phrase.phrase_id!r}")
        root, _ = self._group(character_id)
        folder = self.library.folder(root.entry_id) / PHRASES_DIR
        folder.mkdir(exist_ok=True)
        wav = folder / f"{phrase.phrase_id}.wav"
        tmp_wav = wav.with_name(wav.name + ".tmp")
        shutil.copyfile(audio, tmp_wav)
        os.replace(tmp_wav, wav)
        path = folder / f"{phrase.phrase_id}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(phrase.model_dump_json(indent=2))
        os.replace(tmp, path)

    def delete_phrase(self, character_id: str, phrase_id: str) -> bool:
        """Remove a phrase; False when the character has no such phrase. The JSON goes first."""
        root, _ = self._group(character_id)
        if _PHRASE_ID.fullmatch(phrase_id) is None:
            return False
        folder = self.library.folder(root.entry_id) / PHRASES_DIR
        info = folder / f"{phrase_id}.json"
        if not info.is_file():
            return False
        info.unlink()
        (folder / f"{phrase_id}.wav").unlink(missing_ok=True)
        return True

    def _groups(self) -> dict[str, tuple[Entry, list[Entry]]]:
        entries = self.library.entries()
        by_id = {entry.entry_id: entry for entry in entries}
        roots: dict[str, Entry] = {}
        derived: dict[str, list[Entry]] = {}
        for entry in entries:
            root_id = _root_id(entry, by_id)
            if root_id == entry.entry_id:
                roots[root_id] = entry
            else:
                derived.setdefault(root_id, []).append(entry)
        return {
            root_id: (root, sorted(derived.get(root_id, []), key=lambda entry: (entry.created_at, entry.entry_id)))
            for root_id, root in roots.items()
        }

    def _group(self, character_id: str) -> tuple[Entry, list[Entry]]:
        group = self._groups().get(character_id)
        if group is None:
            raise KeyError(character_id)
        return group

    def _character(self, root: Entry, derived: list[Entry]) -> Character:
        return Character(
            character_id=root.entry_id,
            name=root.name,
            created_at=root.created_at,
            input_file=root.input_file,
            versions=[root.entry_id, *(entry.entry_id for entry in derived)],
            voice_id=self._read_data(root.entry_id).voice_id,
            phrase_count=self._phrase_count(root.entry_id),
        )

    def _data_path(self, root_id: str) -> Path:
        return self.library.folder(root_id) / CHARACTER_FILE

    def _read_data(self, root_id: str) -> CharacterData:
        path = self._data_path(root_id)
        if not path.is_file():
            return CharacterData()
        try:
            return CharacterData.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            logger.warning("Ignoring unreadable character data %s: %s", path, error)
            return CharacterData()

    def _phrase_count(self, root_id: str) -> int:
        folder = self.library.folder(root_id) / PHRASES_DIR
        if not folder.is_dir():
            return 0
        return sum(1 for path in folder.glob("*.json") if path.is_file())
