from .test_api import assert_error
from .test_library import add_entry, make


def test_list_groups_root_and_rigged_into_one_character(tmp_path):
    client, store, lib = make(tmp_path)
    root = add_entry(lib, tmp_path, "hero")
    rigged = lib.create_derived(root.entry_id)

    resp = client.get("/v1/characters")

    assert resp.status_code == 200
    [character] = resp.json()
    assert character["character_id"] == root.entry_id
    assert character["name"] == "hero"
    assert character["input_file"] == "input.png"
    assert character["versions"] == [root.entry_id, rigged.entry_id]
    assert character["voice_id"] is None
    assert character["phrase_count"] == 0


def test_get_returns_one_character(tmp_path):
    client, store, lib = make(tmp_path)
    root = add_entry(lib, tmp_path, "hero")
    rigged = lib.create_derived(root.entry_id)

    resp = client.get(f"/v1/characters/{root.entry_id}")

    assert resp.status_code == 200
    assert resp.json()["versions"] == [root.entry_id, rigged.entry_id]


def test_derived_and_unknown_ids_are_character_not_found(tmp_path):
    client, store, lib = make(tmp_path)
    root = add_entry(lib, tmp_path, "hero")
    rigged = lib.create_derived(root.entry_id)

    error = assert_error(client.get(f"/v1/characters/{rigged.entry_id}"), 404, "character_not_found")
    assert error["message"] == f"No character {rigged.entry_id}"
    assert_error(client.get("/v1/characters/nope"), 404, "character_not_found")
    assert_error(client.get("/v1/characters/-x"), 404, "character_not_found")


def test_patch_renames_the_root_entry(tmp_path):
    client, store, lib = make(tmp_path)
    root = add_entry(lib, tmp_path, "hero")

    resp = client.patch(f"/v1/characters/{root.entry_id}", json={"name": "Ana"})

    assert resp.status_code == 200
    assert resp.json()["name"] == "Ana"
    assert client.get(f"/v1/library/{root.entry_id}").json()["name"] == "Ana"


def test_patch_rejects_blank_names_and_derived_ids(tmp_path):
    client, store, lib = make(tmp_path)
    root = add_entry(lib, tmp_path, "hero")
    rigged = lib.create_derived(root.entry_id)

    assert_error(client.patch(f"/v1/characters/{root.entry_id}", json={"name": "   "}), 400, "invalid_name")
    assert_error(client.patch(f"/v1/characters/{rigged.entry_id}", json={"name": "Ana"}), 404, "character_not_found")


def test_library_api_still_lists_every_entry_unchanged(tmp_path):
    client, store, lib = make(tmp_path)
    root = add_entry(lib, tmp_path, "hero")
    rigged = lib.create_derived(root.entry_id)

    body = client.get("/v1/library").json()

    assert sorted((entry["entry_id"], entry["name"]) for entry in body) == sorted(
        [(root.entry_id, "hero"), (rigged.entry_id, "hero (rigged)")]
    )
