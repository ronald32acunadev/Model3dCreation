import json
import re

from model3d import library

PNG = b"\x89PNG\r\n\x1a\nfake"


def make_image(tmp_path, name="cat.png"):
    image = tmp_path / "inputs" / name
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(PNG)
    return image


def test_create_entry_matches_client_format(tmp_path):
    root = tmp_path / "library"
    options = {"texture": True, "quality": "high", "rig": True}
    entry = library.create_entry(root, make_image(tmp_path, "Cat.PNG"), options)
    assert re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{6}", entry["entry_id"])
    folder = root / entry["entry_id"]
    on_disk = json.loads((folder / "entry.json").read_text())
    assert on_disk == entry
    assert entry["name"] == "Cat"
    assert entry["outcome"] == "running"
    assert entry["input_file"] == "input.png"
    assert entry["options"] == options
    assert entry["created_at"].endswith("Z")
    assert (folder / "input.png").read_bytes() == PNG
    assert library.model_path(root, entry["entry_id"]) == folder / "model.glb"


def test_create_entry_with_source_and_name(tmp_path):
    entry = library.create_entry(tmp_path, make_image(tmp_path), {"rig": True}, name="Cat (rigged)", source_entry_id="X")
    assert (entry["name"], entry["source_entry_id"]) == ("Cat (rigged)", "X")


def test_update_entry_replaces_atomically(tmp_path):
    entry = library.create_entry(tmp_path, make_image(tmp_path), {})
    library.update_entry(tmp_path, {**entry, "outcome": "succeeded"})
    assert json.loads((tmp_path / entry["entry_id"] / "entry.json").read_text())["outcome"] == "succeeded"
    assert not (tmp_path / entry["entry_id"] / "entry.json.tmp").exists()


def test_list_entries_skips_unreadable(tmp_path):
    first = library.create_entry(tmp_path, make_image(tmp_path), {})
    broken = tmp_path / "20261022T120000-abcdef"
    broken.mkdir()
    (broken / "entry.json").write_text("{not json")
    second = library.create_entry(tmp_path, make_image(tmp_path), {})
    assert [entry["entry_id"] for entry in library.list_entries(tmp_path)] == [second["entry_id"], first["entry_id"]]
    assert library.list_entries(tmp_path / "missing") == []
