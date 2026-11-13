import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model3d_contracts.characters import CHARACTER_FILE, PHRASES_DIR, CharacterData, Characters, Phrase
from model3d_contracts.jobs import JobOptions
from model3d_contracts.library import Entry, Library

BASE = datetime(2026, 11, 1, tzinfo=UTC)


def make_library(tmp_path: Path) -> Library:
    return Library(tmp_path / "library", recover=False)


def dated(library: Library, entry: Entry, minutes: int) -> Entry:
    entry = entry.model_copy(update={"created_at": BASE + timedelta(minutes=minutes)})
    library.update(entry)
    return entry


def new_root(library: Library, tmp_path: Path, name: str, minutes: int) -> Entry:
    image = tmp_path / f"{name}.png"
    image.write_bytes(b"png")
    return dated(library, library.create(image, JobOptions(), name=name), minutes)


def test_derived_entries_at_any_depth_join_their_root(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    rigged = dated(library, library.create_derived(root.entry_id), 1)
    walk = dated(library, library.create_derived(rigged.entry_id, " (walk)"), 2)

    [character] = Characters(library).characters()

    assert character.character_id == root.entry_id
    assert character.name == "hero"
    assert character.created_at == root.created_at
    assert character.input_file == "input.png"
    assert character.versions == [root.entry_id, rigged.entry_id, walk.entry_id]


def test_derived_versions_are_ordered_by_creation_time(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    later = dated(library, library.create_derived(root.entry_id), 5)
    earlier = dated(library, library.create_derived(root.entry_id, " (walk)"), 3)

    [character] = Characters(library).characters()

    assert character.versions == [root.entry_id, earlier.entry_id, later.entry_id]


def test_two_roots_are_two_characters_newest_first(tmp_path):
    library = make_library(tmp_path)
    old = new_root(library, tmp_path, "old", 0)
    new = new_root(library, tmp_path, "new", 10)

    assert [character.character_id for character in Characters(library).characters()] == [
        new.entry_id,
        old.entry_id,
    ]


def test_orphan_derived_entry_becomes_its_own_character(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    rigged = dated(library, library.create_derived(root.entry_id), 1)
    library.delete(root.entry_id)
    characters = Characters(library)

    [character] = characters.characters()

    assert character.character_id == rigged.entry_id
    assert character.versions == [rigged.entry_id]
    assert characters.get(root.entry_id) is None


def test_derived_id_is_not_a_character(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    rigged = dated(library, library.create_derived(root.entry_id), 1)
    characters = Characters(library)

    assert characters.get(rigged.entry_id) is None
    character = characters.get(root.entry_id)
    assert character is not None
    assert character.versions == [root.entry_id, rigged.entry_id]


def test_cycle_makes_each_entry_its_own_root(tmp_path):
    library = make_library(tmp_path)
    first = new_root(library, tmp_path, "first", 0)
    second = new_root(library, tmp_path, "second", 1)
    library.update(first.model_copy(update={"source_entry_id": second.entry_id}))
    library.update(second.model_copy(update={"source_entry_id": first.entry_id}))

    characters = Characters(library).characters()

    assert sorted(character.character_id for character in characters) == sorted([first.entry_id, second.entry_id])
    assert all(character.versions == [character.character_id] for character in characters)


def test_existing_library_reads_defaults_and_writes_nothing(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    folder = library.folder(root.entry_id)
    before = sorted(path.name for path in folder.iterdir())
    characters = Characters(library)

    [character] = characters.characters()

    assert character.voice_id is None
    assert character.phrase_count == 0
    assert characters.data(root.entry_id) == CharacterData()
    assert sorted(path.name for path in folder.iterdir()) == before
    assert not (folder / CHARACTER_FILE).exists()


def test_phrase_count_counts_json_files_in_phrases(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    phrases = library.folder(root.entry_id) / PHRASES_DIR
    phrases.mkdir()
    (phrases / "a.json").write_text("{}")
    (phrases / "b.json").write_text("{}")
    (phrases / "c.wav").write_bytes(b"wav")

    character = Characters(library).get(root.entry_id)

    assert character is not None
    assert character.phrase_count == 2


def test_save_data_round_trips_and_defaults_remove_the_file(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)
    path = library.folder(root.entry_id) / CHARACTER_FILE

    characters.save_data(root.entry_id, CharacterData(voice_id="voice-1"))

    assert json.loads(path.read_text()) == {"voice_id": "voice-1"}
    assert characters.data(root.entry_id) == CharacterData(voice_id="voice-1")
    character = characters.get(root.entry_id)
    assert character is not None
    assert character.voice_id == "voice-1"

    characters.save_data(root.entry_id, CharacterData())

    assert not path.exists()
    assert characters.data(root.entry_id) == CharacterData()


def test_corrupt_character_file_reads_as_defaults_with_a_warning(tmp_path, caplog):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    (library.folder(root.entry_id) / CHARACTER_FILE).write_text("{not json")
    characters = Characters(library)

    with caplog.at_level(logging.WARNING, logger="model3d_contracts.characters"):
        data = characters.data(root.entry_id)

    assert data == CharacterData()
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_rename_changes_the_root_entry_name(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)

    character = characters.rename(root.entry_id, "  Ana  ")

    assert character.name == "Ana"
    [entry] = [entry for entry in library.entries() if entry.entry_id == root.entry_id]
    assert entry.name == "Ana"


def test_rename_rejects_blank_names_and_unknown_ids(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    rigged = dated(library, library.create_derived(root.entry_id), 1)
    characters = Characters(library)

    with pytest.raises(ValueError):
        characters.rename(root.entry_id, "   ")
    with pytest.raises(KeyError):
        characters.rename(rigged.entry_id, "Ana")
    with pytest.raises(KeyError):
        characters.data("missing")


def make_phrase(phrase_id: str, minutes: int, text: str = "Hello") -> Phrase:
    return Phrase(phrase_id=phrase_id, text=text, language="en", created_at=BASE + timedelta(minutes=minutes))


def test_phrases_round_trip_newest_first_and_count(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF-a")

    characters.add_phrase(root.entry_id, make_phrase("OLD", 1), wav)
    characters.add_phrase(root.entry_id, make_phrase("NEW", 2, "Newer"), wav)

    assert [(p.phrase_id, p.text, p.language) for p in characters.phrases(root.entry_id)] == [
        ("NEW", "Newer", "en"),
        ("OLD", "Hello", "en"),
    ]
    audio = characters.phrase_audio(root.entry_id, "NEW")
    assert audio == library.folder(root.entry_id) / PHRASES_DIR / "NEW.wav"
    assert audio.read_bytes() == b"RIFF-a"
    assert characters.get(root.entry_id).phrase_count == 2


def test_add_phrase_writes_json_after_the_audio(tmp_path, monkeypatch):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF-a")

    real_replace = os.replace

    def broken_replace(source, target):
        if str(target).endswith(".json"):
            raise OSError("disk full")
        return real_replace(source, target)

    monkeypatch.setattr("model3d_contracts.characters.os.replace", broken_replace)
    with pytest.raises(OSError):
        characters.add_phrase(root.entry_id, make_phrase("P1", 1), wav)

    assert characters.get(root.entry_id).phrase_count == 0
    assert characters.phrases(root.entry_id) == []
    assert characters.phrase_audio(root.entry_id, "P1") is None


def test_delete_phrase_removes_both_files_and_reports_missing(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF-a")
    characters.add_phrase(root.entry_id, make_phrase("P1", 1), wav)

    assert characters.delete_phrase(root.entry_id, "P1") is True
    assert characters.delete_phrase(root.entry_id, "P1") is False
    folder = library.folder(root.entry_id) / PHRASES_DIR
    assert list(folder.iterdir()) == []


def test_phrase_ids_are_validated_before_touching_disk(tmp_path):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF-a")
    outside = library.folder(root.entry_id) / "secret.json"
    outside.write_text("{}")

    with pytest.raises(ValueError):
        characters.add_phrase(root.entry_id, make_phrase("../x", 1), wav)
    assert characters.phrase_audio(root.entry_id, "../secret") is None
    assert characters.delete_phrase(root.entry_id, "../secret") is False
    assert outside.exists()


def test_unreadable_phrase_is_skipped_with_a_warning(tmp_path, caplog):
    library = make_library(tmp_path)
    root = new_root(library, tmp_path, "hero", 0)
    characters = Characters(library)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF-a")
    characters.add_phrase(root.entry_id, make_phrase("GOOD", 1), wav)
    (library.folder(root.entry_id) / PHRASES_DIR / "bad.json").write_text("{not json")

    with caplog.at_level(logging.WARNING):
        listed = characters.phrases(root.entry_id)

    assert [p.phrase_id for p in listed] == ["GOOD"]
    assert "Ignoring unreadable phrase" in caplog.text


def test_clear_voice_unlinks_only_the_characters_using_it(tmp_path):
    library = make_library(tmp_path)
    one = new_root(library, tmp_path, "one", 0)
    two = new_root(library, tmp_path, "two", 1)
    three = new_root(library, tmp_path, "three", 2)
    characters = Characters(library)
    characters.save_data(one.entry_id, CharacterData(voice_id="v1"))
    characters.save_data(two.entry_id, CharacterData(voice_id="v1"))
    characters.save_data(three.entry_id, CharacterData(voice_id="v2"))

    cleared = characters.clear_voice("v1")

    assert sorted(cleared) == sorted([one.entry_id, two.entry_id])
    assert characters.get(one.entry_id).voice_id is None
    assert characters.get(two.entry_id).voice_id is None
    assert characters.get(three.entry_id).voice_id == "v2"
    assert not (library.folder(one.entry_id) / CHARACTER_FILE).exists()
    assert characters.clear_voice("v1") == []
