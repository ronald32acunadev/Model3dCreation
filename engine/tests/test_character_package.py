import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from model3d_contracts.characters import Characters, Phrase
from model3d_contracts.library import Library, Outcome
from model3d_engine.voices import VoiceStore

from .test_api import assert_error
from .test_library import add_entry
from .test_speech import make

PHRASE_ID = "job-1"
WAV = b"RIFFphrase-audio"


def build(tmp_path, with_voice=True, with_phrase=True):
    client, _, _, voice = make(tmp_path)
    library = Library(tmp_path / "library", recover=False)
    characters = Characters(library)
    hero = add_entry(library, tmp_path, "hero")
    rigged = library.create_derived(hero.entry_id)
    library.model_path(rigged.entry_id).write_bytes(b"glb-rigged")
    library.update(rigged.model_copy(update={"outcome": Outcome.succeeded}))
    (library.folder(hero.entry_id) / "model_original.glb").write_bytes(b"glb-original")
    export = library.folder(rigged.entry_id) / "exports" / "fbx"
    export.mkdir(parents=True)
    (export / "model.fbx").write_bytes(b"fbx-bytes")
    if with_voice:
        characters.save_data(hero.entry_id, characters.data(hero.entry_id).model_copy(update={"voice_id": voice.voice_id}))
    if with_phrase:
        audio = tmp_path / "said.wav"
        audio.write_bytes(WAV)
        phrase = Phrase(phrase_id=PHRASE_ID, text="Hola mundo.", language="es", created_at=datetime.now(UTC))
        characters.add_phrase(hero.entry_id, phrase, audio)
    return client, library, characters, voice, hero, rigged


def export(client, character_id):
    response = client.get(f"/v1/characters/{character_id}/package")
    assert response.status_code == 200, response.text
    return response


def upload(client, data):
    return client.post("/v1/characters/import", files={"package": ("x.m3dchar", data, "application/zip")})


def snapshot(tmp_path):
    return sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))


def repack(data, edit):
    source = zipfile.ZipFile(io.BytesIO(data))
    members = {name: source.read(name) for name in source.namelist()}
    edit(members)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return out.getvalue()


def edit_manifest(change):
    def edit(members):
        manifest = json.loads(members["manifest.json"])
        change(manifest)
        members["manifest.json"] = json.dumps(manifest).encode()

    return edit


def test_export_holds_manifest_and_every_file(tmp_path):
    client, library, _, voice, hero, rigged = build(tmp_path)
    with client:
        response = export(client, hero.entry_id)

    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="hero.m3dchar"'
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    h, r = hero.entry_id, rigged.entry_id
    assert sorted(archive.namelist()) == sorted([
        "manifest.json",
        f"entries/{h}/input.png", f"entries/{h}/model.glb", f"entries/{h}/model_original.glb",
        f"entries/{r}/input.png", f"entries/{r}/model.glb", f"entries/{r}/exports/fbx/model.fbx",
        "voice/sample.wav",
        f"phrases/{PHRASE_ID}.wav",
    ])
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == 1
    assert manifest["name"] == "hero"
    assert [entry["entry_id"] for entry in manifest["entries"]] == [h, r]
    assert [entry["source_entry_id"] for entry in manifest["entries"]] == [None, h]
    assert manifest["entries"][1]["files"] == ["exports/fbx/model.fbx", "input.png", "model.glb"]
    assert manifest["voice"]["name"] == "Ana"
    assert manifest["voice"]["language"] == "es"
    assert manifest["voice"]["transcript"] == "Hola, soy Ana."
    assert [phrase["text"] for phrase in manifest["phrases"]] == ["Hola mundo."]
    assert archive.read("voice/sample.wav") == b"RIFFsample"
    assert voice.voice_id not in json.dumps(manifest)


def test_export_without_voice_or_phrases(tmp_path):
    client, _, _, _, hero, _ = build(tmp_path, with_voice=False, with_phrase=False)
    with client:
        response = export(client, hero.entry_id)

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["voice"] is None
    assert manifest["phrases"] == []
    assert not any(name.startswith(("voice/", "phrases/")) for name in archive.namelist())


def test_export_of_unknown_or_derived_character_is_404(tmp_path):
    client, _, _, _, hero, rigged = build(tmp_path)
    with client:
        assert_error(client.get("/v1/characters/nope/package"), 404, "character_not_found")
        assert_error(client.get(f"/v1/characters/{rigged.entry_id}/package"), 404, "character_not_found")


def test_round_trip_keeps_everything(tmp_path):
    client, library, characters, voice, hero, rigged = build(tmp_path)
    with client:
        package = export(client, hero.entry_id).content
        response = upload(client, package)
        assert response.status_code == 201, response.text
        imported = response.json()
        voice_list = client.get("/v1/voices").json()

    assert imported["character_id"] != hero.entry_id
    assert imported["name"] == "hero"
    assert len(imported["versions"]) == 2
    assert not {hero.entry_id, rigged.entry_id} & set(imported["versions"])
    new_root, new_rigged = (library.folder(entry_id) for entry_id in imported["versions"])
    entries = {entry.entry_id: entry for entry in library.entries()}
    assert entries[imported["versions"][0]].source_entry_id is None
    assert entries[imported["versions"][1]].source_entry_id == imported["versions"][0]
    assert entries[imported["versions"][1]].name == "hero (rigged)"
    for old, new in ((hero, new_root), (rigged, new_rigged)):
        old_folder = library.folder(old.entry_id)
        old_files = sorted(p.relative_to(old_folder) for p in old_folder.rglob("*") if p.is_file() and p.name != "entry.json")
        new_files = sorted(p.relative_to(new) for p in new.rglob("*") if p.is_file() and p.name != "entry.json")
        old_files = [p for p in old_files if p.parts[0] not in ("character.json", "phrases")]
        new_files = [p for p in new_files if p.parts[0] not in ("character.json", "phrases")]
        assert new_files == old_files
        assert all((new / p).read_bytes() == (old_folder / p).read_bytes() for p in old_files)
    assert imported["voice_id"] != voice.voice_id
    assert [item["voice_id"] for item in voice_list if item["name"] == "Ana"].count(imported["voice_id"]) == 1
    new_voice = VoiceStore(tmp_path / "voices").get(imported["voice_id"])
    assert new_voice.transcript == "Hola, soy Ana."
    assert VoiceStore(tmp_path / "voices").sample_path(new_voice.voice_id).read_bytes() == b"RIFFsample"
    phrases = characters.phrases(imported["character_id"])
    assert [phrase.text for phrase in phrases] == ["Hola mundo."]
    assert phrases[0].phrase_id != PHRASE_ID
    assert characters.phrase_audio(imported["character_id"], phrases[0].phrase_id).read_bytes() == WAV


def test_importing_twice_makes_two_characters(tmp_path):
    client, _, characters, _, hero, _ = build(tmp_path)
    with client:
        package = export(client, hero.entry_id).content
        first, second = upload(client, package).json(), upload(client, package).json()

    assert first["character_id"] != second["character_id"]
    assert len(characters.characters()) == 3


@pytest.mark.parametrize(
    ("label", "edit", "reason"),
    [
        ("format", edit_manifest(lambda m: m.update(format=2)), "Unsupported package format 2"),
        ("missing file", lambda members: members.pop("voice/sample.wav"), "Missing file in package: voice/sample.wav"),
        ("no manifest", lambda members: members.pop("manifest.json"), "Missing file in package: manifest.json"),
        ("broken manifest", lambda members: members.update({"manifest.json": b"{nope"}), "Unreadable manifest"),
        (
            "dotdot file",
            edit_manifest(lambda m: m["entries"][0]["files"].append("../../escape.txt")),
            "Unsafe path in package: entries/",
        ),
        (
            "absolute file",
            edit_manifest(lambda m: m["entries"][0]["files"].append("/etc/passwd")),
            "Unsafe path in package: entries/",
        ),
        (
            "backslash file",
            edit_manifest(lambda m: m["entries"][0]["files"].append("a\\b.txt")),
            "Unsafe path in package: entries/",
        ),
        ("unsafe member", lambda members: members.update({"../evil.txt": b"x"}), "Unsafe path in package: ../evil.txt"),
        (
            "orphan source",
            edit_manifest(lambda m: m["entries"][1].update(source_entry_id="ghost")),
            "derives from a missing entry",
        ),
        (
            "root with source",
            edit_manifest(lambda m: m["entries"][0].update(source_entry_id=m["entries"][1]["entry_id"])),
            "must not derive",
        ),
    ],
)
def test_invalid_package_is_rejected_and_writes_nothing(tmp_path, label, edit, reason):
    client, _, _, _, hero, _ = build(tmp_path)
    with client:
        package = repack(export(client, hero.entry_id).content, edit)
        before = snapshot(tmp_path)

        error = assert_error(upload(client, package), 400, "invalid_package")

        assert reason in error["message"], label
        assert snapshot(tmp_path) == before


def test_not_a_zip_and_missing_package_are_rejected(tmp_path):
    client, _, _, _, _, _ = build(tmp_path)
    with client:
        before = snapshot(tmp_path)
        error = assert_error(upload(client, b"plain text"), 400, "invalid_package")
        assert error["message"] == "Not a .m3dchar package"
        assert_error(client.post("/v1/characters/import"), 400, "invalid_package")
        assert snapshot(tmp_path) == before


def test_write_failure_leaves_nothing_behind(tmp_path, monkeypatch):
    client, library, characters, _, hero, _ = build(tmp_path)
    with client:
        package = export(client, hero.entry_id).content
        before = snapshot(tmp_path)

        def fail(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(VoiceStore, "create", fail)
        with pytest.raises(OSError, match="disk full"):
            upload(client, package)

    assert snapshot(tmp_path) == before
    assert len(characters.characters()) == 1
