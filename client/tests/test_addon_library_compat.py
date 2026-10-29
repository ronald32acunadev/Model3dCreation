"""The Blender add-on writes library entries without this package; the app must read them."""

import importlib.util
from pathlib import Path

from model3d_client.services.library import Library, Outcome

ADDON_LIBRARY = Path(__file__).resolve().parents[2] / "blender/model3d/library.py"


def test_addon_entry_reads_as_client_entry(tmp_path, png):
    spec = importlib.util.spec_from_file_location("addon_library", ADDON_LIBRARY)
    addon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(addon)
    options = {"texture": True, "quality": "final", "seed": None, "remove_background": True,
               "target_faces": None, "rig": True}
    entry = addon.create_entry(tmp_path, png, options, source_entry_id="20261022T035105-213863")
    addon.update_entry(tmp_path, {**entry, "outcome": "succeeded", "job_id": "J1", "seed": 7})
    [read] = Library(tmp_path).entries()
    assert read.entry_id == entry["entry_id"]
    assert read.outcome == Outcome.succeeded
    assert read.options.rig is True
    assert read.source_entry_id == "20261022T035105-213863"
    assert Library(tmp_path).input_path(read.entry_id).read_bytes() == png.read_bytes()
