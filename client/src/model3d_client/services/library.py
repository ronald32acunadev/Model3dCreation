"""The app's library of generations: one folder per entry under data/library."""

import logging
import os
import secrets
import shutil
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobOptions
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)
ENTRY_FILE = "entry.json"
MODEL_FILE = "model.glb"


class Outcome(str, Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"  # the app stopped while the job ran


class Entry(BaseModel):
    entry_id: str
    name: str
    created_at: datetime
    options: JobOptions
    seed: int | None = None
    job_id: str | None = None
    outcome: Outcome
    error: JobError | None = None
    stats: ResultStats | None = None
    input_file: str
    source_entry_id: str | None = None  # the entry this one added a skeleton to


class Library:
    """Entries live in separate folders, so one unreadable entry never hides the others."""

    def __init__(self, root: Path):
        self.root = root
        # A job that was running when the app last stopped will never report back.
        for entry in self.entries():
            if entry.outcome == Outcome.running:
                self.update(entry.model_copy(update={"outcome": Outcome.interrupted}))

    def folder(self, entry_id: str) -> Path:
        return self.root / entry_id

    def model_path(self, entry_id: str) -> Path:
        return self.folder(entry_id) / MODEL_FILE

    def input_path(self, entry_id: str) -> Path:
        return self.folder(entry_id) / self._read(entry_id).input_file

    def entries(self) -> list[Entry]:
        """All readable entries, newest first."""
        if not self.root.is_dir():
            return []
        found = []
        for folder in self.root.iterdir():
            if (folder / ENTRY_FILE).is_file():
                try:
                    found.append(self._read(folder.name))
                except (OSError, ValidationError) as error:
                    logger.warning("Skipping unreadable library entry %s: %s", folder, error)
        return sorted(found, key=lambda entry: (entry.created_at, entry.entry_id), reverse=True)

    def create(self, image: Path, options: JobOptions) -> Entry:
        """Copy the image into a new entry that is running."""
        now = datetime.now(UTC)
        entry_id = f"{now:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
        input_file = "input" + image.suffix.lower()
        self.folder(entry_id).mkdir(parents=True)
        shutil.copyfile(image, self.folder(entry_id) / input_file)
        entry = Entry(
            entry_id=entry_id,
            name=image.stem,
            created_at=now,
            options=options,
            outcome=Outcome.running,
            input_file=input_file,
        )
        self.update(entry)
        return entry

    def create_derived(self, source_id: str, suffix: str = " (rigged)") -> Entry:
        """A new running entry that will hold the source's model with a skeleton; the source is untouched."""
        source = self._read(source_id)
        now = datetime.now(UTC)
        entry_id = f"{now:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
        self.folder(entry_id).mkdir(parents=True)
        shutil.copyfile(self.folder(source_id) / source.input_file, self.folder(entry_id) / source.input_file)
        entry = Entry(
            entry_id=entry_id,
            name=source.name + suffix,
            created_at=now,
            options=source.options.model_copy(update={"rig": True}),
            outcome=Outcome.running,
            input_file=source.input_file,
            source_entry_id=source_id,
        )
        self.update(entry)
        return entry

    @staticmethod
    def has_skeleton(entry: Entry) -> bool:
        return entry.options.rig

    def update(self, entry: Entry) -> None:
        path = self.folder(entry.entry_id) / ENTRY_FILE
        tmp = path.with_name(ENTRY_FILE + ".tmp")
        tmp.write_text(entry.model_dump_json(indent=2))
        os.replace(tmp, path)

    def rename(self, entry_id: str, name: str) -> Entry:
        name = name.strip()
        if not name:
            raise ValueError("Name must not be empty")
        entry = self._read(entry_id).model_copy(update={"name": name})
        self.update(entry)
        return entry

    def delete(self, entry_id: str) -> None:
        shutil.rmtree(self.folder(entry_id))

    def _read(self, entry_id: str) -> Entry:
        return Entry.model_validate_json((self.folder(entry_id) / ENTRY_FILE).read_bytes())
