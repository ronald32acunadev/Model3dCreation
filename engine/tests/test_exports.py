import io
import zipfile

from .test_api import assert_error
from .tool_stubs import add_model_entry, make_tool_client, set_mode, wait_for


def test_export_single_file_format_downloads_the_written_bytes(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    entry = add_model_entry(lib, tmp_path, "single")
    with client:
        response = client.post(f"/v1/library/{entry.entry_id}/exports", json={"format": "fbx"})
        assert response.status_code == 202
        assert response.json() == {"format": "fbx", "state": "running"}
        url = f"/v1/library/{entry.entry_id}/exports/fbx"
        done = wait_for(client, url, lambda r: r.status_code == 200)
        assert done.content == b"converted-fbx"


def test_export_of_several_files_downloads_a_zip(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    set_mode(stubs, "multi")
    entry = add_model_entry(lib, tmp_path, "multi")
    with client:
        response = client.post(f"/v1/library/{entry.entry_id}/exports", json={"format": "obj"})
        assert response.status_code == 202
        url = f"/v1/library/{entry.entry_id}/exports/obj"
        done = wait_for(client, url, lambda r: r.status_code == 200)
        assert done.headers["content-type"].startswith("application/zip")
        zf = zipfile.ZipFile(io.BytesIO(done.content))
        assert sorted(zf.namelist()) == ["model.mtl", "model.obj", "texture.png"]
        assert zf.read("model.obj") == b"converted-obj"
        assert zf.read("model.mtl") == b"mtl"
        assert zf.read("texture.png") == b"png"


def test_unknown_format_is_rejected_listing_the_valid_ones(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    entry = add_model_entry(lib, tmp_path, "fmt")
    with client:
        response = client.post(f"/v1/library/{entry.entry_id}/exports", json={"format": "gltf"})
        err = assert_error(response, 422, "invalid_format")
        message = err["message"]
        assert "fbx" in message
        assert "obj" in message
        assert "stl" in message
        assert "usdz" in message


def test_unknown_entry_is_not_found(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        response = client.post("/v1/library/missing-1/exports", json={"format": "fbx"})
        assert_error(response, 404, "entry_not_found")
        response = client.get("/v1/library/missing-1/exports/fbx")
        assert_error(response, 404, "entry_not_found")


def test_entry_without_a_model_is_not_ready(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    entry = add_model_entry(lib, tmp_path, "nomodel")
    lib.model_path(entry.entry_id).unlink()
    with client:
        response = client.post(f"/v1/library/{entry.entry_id}/exports", json={"format": "fbx"})
        assert_error(response, 409, "model_not_ready")
        response = client.get(f"/v1/library/{entry.entry_id}/exports/fbx")
        err = assert_error(response, 409, "export_not_ready")
        assert err["message"] == "Export fbx was not started"


def test_download_before_the_export_starts_is_not_ready(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    entry = add_model_entry(lib, tmp_path, "ready")
    with client:
        response = client.get(f"/v1/library/{entry.entry_id}/exports/fbx")
        err = assert_error(response, 409, "export_not_ready")
        assert err["message"] == "Export fbx was not started"


def test_failed_export_reports_the_tool_error(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    set_mode(stubs, "fail")
    entry = add_model_entry(lib, tmp_path, "fail")
    with client:
        response = client.post(f"/v1/library/{entry.entry_id}/exports", json={"format": "stl"})
        assert response.status_code == 202
        url = f"/v1/library/{entry.entry_id}/exports/stl"
        done = wait_for(client, url, lambda r: r.status_code == 409 and "failed" in r.json()["error"]["message"])
        err = assert_error(done, 409, "export_not_ready")
        assert "Could not write" in err["message"]
        folder = lib.folder(entry.entry_id) / "exports" / "stl"
        assert not any(p.is_file() for p in folder.rglob("*"))


def test_invalid_format_leaves_the_entry_untouched(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    entry = add_model_entry(lib, tmp_path, "untouched")
    entry_json = lib.folder(entry.entry_id) / "entry.json"
    before = entry_json.read_bytes()
    with client:
        response = client.post(f"/v1/library/{entry.entry_id}/exports", json={"format": "gltf"})
        assert_error(response, 422, "invalid_format")
        assert entry_json.read_bytes() == before
        assert not (lib.folder(entry.entry_id) / "exports").exists()
