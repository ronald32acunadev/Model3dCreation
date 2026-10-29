import pytest
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobOptions, Quality

from model3d_client.services.library import Library, Outcome


def test_create_copies_image_and_lists_newest_first(tmp_path, png):
    lib = Library(tmp_path / "lib")
    first = lib.create(png, JobOptions())
    second = lib.create(png, JobOptions(quality=Quality.draft))
    assert first.name == "penguin"
    assert first.outcome == Outcome.running
    assert lib.input_path(first.entry_id).read_bytes() == png.read_bytes()
    assert lib.input_path(first.entry_id).name == "input.png"
    assert lib.model_path(first.entry_id) == lib.folder(first.entry_id) / "model.glb"
    assert [e.entry_id for e in lib.entries()] == [second.entry_id, first.entry_id]
    assert lib.entries()[0].options.quality == Quality.draft


def test_update_and_rename_persist(tmp_path, png):
    lib = Library(tmp_path)
    entry = lib.create(png, JobOptions())
    stats = ResultStats(vertices=10, faces=20, textured=True, duration_s=135.0)
    lib.update(entry.model_copy(update={"outcome": Outcome.succeeded, "stats": stats, "seed": 7, "job_id": "J1"}))
    lib.rename(entry.entry_id, "  Mi pingüino  ")
    loaded = Library(tmp_path).entries()[0]
    assert loaded.outcome == Outcome.succeeded
    assert loaded.stats == stats
    assert (loaded.seed, loaded.job_id, loaded.name) == (7, "J1", "Mi pingüino")


def test_rename_rejects_empty_name(tmp_path, png):
    lib = Library(tmp_path)
    entry = lib.create(png, JobOptions())
    with pytest.raises(ValueError):
        lib.rename(entry.entry_id, "   ")


def test_failed_entry_keeps_error(tmp_path, png):
    lib = Library(tmp_path)
    entry = lib.create(png, JobOptions())
    lib.update(entry.model_copy(update={"outcome": Outcome.failed, "error": JobError(code="x", message="boom")}))
    assert lib.entries()[0].error.message == "boom"


def test_delete_removes_folder(tmp_path, png):
    lib = Library(tmp_path)
    entry = lib.create(png, JobOptions())
    lib.delete(entry.entry_id)
    assert not lib.folder(entry.entry_id).exists()
    assert lib.entries() == []


def test_corrupt_entry_is_skipped(tmp_path, png):
    lib = Library(tmp_path)
    good = lib.create(png, JobOptions())
    bad = lib.create(png, JobOptions())
    (lib.folder(bad.entry_id) / "entry.json").write_text("garbage")
    assert [e.entry_id for e in Library(tmp_path).entries()] == [good.entry_id]


def test_running_entries_become_interrupted_on_load(tmp_path, png):
    lib = Library(tmp_path)
    e = lib.create(png, JobOptions())
    assert Library(tmp_path).entries()[0].outcome == Outcome.interrupted
    assert lib.entries()[0].entry_id == e.entry_id
    assert lib.entries()[0].outcome == Outcome.interrupted  # persisted, not only in memory


def test_create_derived_copies_input_and_links_source(tmp_path, png):
    lib = Library(tmp_path)
    source = lib.create(png, JobOptions(quality=Quality.high))
    lib.update(source.model_copy(update={"outcome": Outcome.succeeded}))
    source_json = (lib.folder(source.entry_id) / "entry.json").read_bytes()
    derived = lib.create_derived(source.entry_id)
    assert derived.entry_id != source.entry_id
    assert derived.name == "penguin (rigged)"
    assert derived.source_entry_id == source.entry_id
    assert derived.options == JobOptions(quality=Quality.high, rig=True)
    assert derived.outcome == Outcome.running
    assert lib.input_path(derived.entry_id).read_bytes() == png.read_bytes()
    assert (lib.folder(source.entry_id) / "entry.json").read_bytes() == source_json
    assert lib.has_skeleton(derived) and not lib.has_skeleton(lib._read(source.entry_id))
