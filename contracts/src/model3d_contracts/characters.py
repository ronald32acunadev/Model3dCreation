"""Characters: a root library entry, every entry derived from it, and the data stored with them."""

import logging
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from model3d_contracts.library import Entry, Library

logger = logging.getLogger(__name__)
CHARACTER_FILE = "character.json"
PHRASES_DIR = "phrases"


class CharacterData(BaseModel):
    """What a character stores beside its versions; US-09 adds fields."""

    voice_id: str | None = None


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
