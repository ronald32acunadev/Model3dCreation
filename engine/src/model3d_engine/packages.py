"""Character packages (.m3dchar): a zip holding a character's versions, voice and phrases."""

import io
import json
import re
import secrets
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from model3d_contracts.characters import CHARACTER_FILE, PHRASES_DIR, Character, Characters, Phrase
from model3d_contracts.library import ENTRY_FILE, Entry, Library
from model3d_engine.voices import VoiceKind, VoiceStore

PACKAGE_FORMAT = 1
PACKAGE_SUFFIX = ".m3dchar"
MANIFEST = "manifest.json"
VOICE_SAMPLE = "voice/sample.wav"
MAX_PACKAGE_BYTES = 2 * 1024**3
_SEGMENT = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")
_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{0,63}")
_CHUNK = 1024 * 1024


class PackageError(ValueError):
    """The package cannot be imported; the message says why."""


class PackagedEntry(Entry):
    model_config = ConfigDict(extra="forbid")
    files: list[str]  # paths inside the entry folder, entry.json excluded


class PackagedVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: VoiceKind
    language: Literal["es", "en"]
    transcript: str
    created_at: datetime


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: int
    name: str
    created_at: datetime
    entries: list[PackagedEntry]  # entries[0] is the root, the rest keep the character's version order
    voice: PackagedVoice | None
    phrases: list[Phrase]


def _entry_files(folder: Path) -> list[str]:
    found = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        parts = relative.split("/")
        if not path.is_file() or relative in (ENTRY_FILE, CHARACTER_FILE) or parts[0] == PHRASES_DIR:
            continue
        if path.name.endswith(".tmp") or not all(_SEGMENT.fullmatch(part) for part in parts):
            continue
        found.append(relative)
    return found


def export_package(character_id: str, library: Library, characters: Characters, voices: VoiceStore) -> bytes:
    """The .m3dchar bytes of a character; a voice that no longer exists is left out."""
    character = characters.get(character_id)
    if character is None:
        raise KeyError(character_id)
    by_id = {entry.entry_id: entry for entry in library.entries()}
    entries = [by_id[entry_id] for entry_id in character.versions]
    voice = voices.get(character.voice_id) if character.voice_id is not None else None
    phrases = [phrase for phrase in characters.phrases(character_id) if characters.phrase_audio(character_id, phrase.phrase_id)]
    packaged = [
        PackagedEntry(**entry.model_dump(), files=_entry_files(library.folder(entry.entry_id))) for entry in entries
    ]
    manifest = Manifest(
        format=PACKAGE_FORMAT,
        name=character.name,
        created_at=character.created_at,
        entries=packaged,
        voice=None
        if voice is None
        else PackagedVoice(
            name=voice.name, kind=voice.kind, language=voice.language, transcript=voice.transcript, created_at=voice.created_at
        ),
        phrases=phrases,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, manifest.model_dump_json(indent=2))
        for entry in packaged:
            for name in entry.files:
                archive.write(library.folder(entry.entry_id) / name, f"entries/{entry.entry_id}/{name}")
        if voice is not None:
            archive.write(voices.sample_path(voice.voice_id), VOICE_SAMPLE)
        for phrase in phrases:
            archive.write(characters.phrase_audio(character_id, phrase.phrase_id), f"phrases/{phrase.phrase_id}.wav")
    return buffer.getvalue()


def _is_safe(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        return False
    parts = name.rstrip("/").split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _read_manifest(archive: zipfile.ZipFile) -> Manifest:
    try:
        raw = archive.read(MANIFEST)
    except KeyError as error:
        raise PackageError(f"Missing file in package: {MANIFEST}") from error
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError, EOFError) as error:
        raise PackageError(f"Unreadable manifest: {error}") from error
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise PackageError(f"Unreadable manifest: {error}") from error
    if isinstance(document, dict) and document.get("format") != PACKAGE_FORMAT:
        raise PackageError(f"Unsupported package format {document.get('format')}")
    try:
        manifest = Manifest.model_validate(document)
    except ValidationError as error:
        raise PackageError(f"Invalid manifest: {error.errors()[0]['msg']}") from error
    return manifest


def _checked(manifest: Manifest, archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Every file the manifest needs, by its path in the zip; raises before anything is written."""
    members = {info.filename: info for info in archive.infolist()}
    for name in members:
        if not _is_safe(name):
            raise PackageError(f"Unsafe path in package: {name}")
    if not manifest.entries:
        raise PackageError("Invalid manifest: no entries")
    ids = [entry.entry_id for entry in manifest.entries]
    if len(set(ids)) != len(ids):
        raise PackageError("Invalid manifest: duplicate entry ids")
    if manifest.entries[0].source_entry_id is not None:
        raise PackageError("Invalid manifest: the first entry must not derive from another")
    needed: list[str] = []
    for entry in manifest.entries:
        if _ID.fullmatch(entry.entry_id) is None:
            raise PackageError(f"Unsafe path in package: {entry.entry_id}")
        if entry.source_entry_id is not None and entry.source_entry_id not in ids:
            raise PackageError(f"Invalid manifest: entry {entry.entry_id} derives from a missing entry")
        if entry.input_file not in entry.files:
            raise PackageError(f"Missing file in package: entries/{entry.entry_id}/{entry.input_file}")
        for name in entry.files:
            parts = name.split("/")
            if name == ENTRY_FILE or not all(_SEGMENT.fullmatch(part) for part in parts):
                raise PackageError(f"Unsafe path in package: entries/{entry.entry_id}/{name}")
            needed.append(f"entries/{entry.entry_id}/{name}")
    by_id = {entry.entry_id: entry for entry in manifest.entries}
    for entry in manifest.entries:
        seen, current = {entry.entry_id}, entry
        while current.source_entry_id is not None:
            current = by_id[current.source_entry_id]
            if current.entry_id in seen:
                raise PackageError(f"Invalid manifest: entry {entry.entry_id} derives from itself")
            seen.add(current.entry_id)
    if manifest.voice is not None:
        needed.append(VOICE_SAMPLE)
    phrase_ids = [phrase.phrase_id for phrase in manifest.phrases]
    if len(set(phrase_ids)) != len(phrase_ids):
        raise PackageError("Invalid manifest: duplicate phrase ids")
    for phrase_id in phrase_ids:
        if _ID.fullmatch(phrase_id) is None:
            raise PackageError(f"Unsafe path in package: phrases/{phrase_id}.wav")
        needed.append(f"phrases/{phrase_id}.wav")
    found: dict[str, zipfile.ZipInfo] = {}
    for name in needed:
        info = members.get(name)
        if info is None or info.is_dir():
            raise PackageError(f"Missing file in package: {name}")
        found[name] = info
    if sum(info.file_size for info in found.values()) > MAX_PACKAGE_BYTES:
        raise PackageError("Package is too large")
    return found


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    """Copy a member to disk, never writing more than its declared size."""
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with archive.open(info) as source, target.open("wb") as out:
            while chunk := source.read(_CHUNK):
                written += len(chunk)
                if written > info.file_size:
                    raise PackageError(f"Corrupt file in package: {info.filename}")
                out.write(chunk)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as error:
        raise PackageError(f"Corrupt file in package: {info.filename}") from error


def _new_entry_id(library: Library) -> str:
    while True:
        entry_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
        if not library.folder(entry_id).exists():
            return entry_id


def import_package(data: bytes, library: Library, characters: Characters, voices: VoiceStore) -> Character:
    """Validate the whole package, then create the character; a failure leaves nothing behind."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise PackageError("Not a .m3dchar package") from error
    with archive:
        manifest = _read_manifest(archive)
        members = _checked(manifest, archive)
        new_ids: dict[str, str] = {}
        folders: list[Path] = []
        voice_id: str | None = None
        try:
            for entry in manifest.entries:
                new_ids[entry.entry_id] = _new_entry_id(library)
                folder = library.folder(new_ids[entry.entry_id])
                folder.mkdir(parents=True)
                folders.append(folder)
                for name in entry.files:
                    _extract(archive, members[f"entries/{entry.entry_id}/{name}"], folder / PurePosixPath(name))
            for entry in manifest.entries:
                fields = entry.model_dump(exclude={"files"})
                fields.update(
                    entry_id=new_ids[entry.entry_id],
                    job_id=None,
                    source_entry_id=None if entry.source_entry_id is None else new_ids[entry.source_entry_id],
                )
                library.update(Entry(**fields))
            root_id = new_ids[manifest.entries[0].entry_id]
            scratch = library.folder(root_id) / "import.tmp"
            if manifest.voice is not None:
                _extract(archive, members[VOICE_SAMPLE], scratch)
                voice = voices.create(
                    manifest.voice.name,
                    manifest.voice.kind,
                    manifest.voice.language,
                    manifest.voice.transcript,
                    scratch.read_bytes(),
                )
                voice_id = voice.voice_id
                characters.save_data(root_id, characters.data(root_id).model_copy(update={"voice_id": voice_id}))
            for phrase in manifest.phrases:
                _extract(archive, members[f"phrases/{phrase.phrase_id}.wav"], scratch)
                characters.add_phrase(
                    root_id, phrase.model_copy(update={"phrase_id": secrets.token_hex(8)}), scratch
                )
            scratch.unlink(missing_ok=True)
        except BaseException:
            for folder in folders:
                shutil.rmtree(folder, ignore_errors=True)
            if voice_id is not None:
                shutil.rmtree(voices.folder(voice_id), ignore_errors=True)
            raise
    character = characters.get(root_id)
    if character is None:
        raise PackageError("The imported character cannot be read")
    return character
