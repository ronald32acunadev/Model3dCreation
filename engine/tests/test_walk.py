from .test_api import assert_error
from .tool_stubs import add_model_entry, make_tool_client, set_mode, wait_for


def test_body_returns_the_detected_body_type(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=True)
        response = client.get(f"/v1/library/{source.entry_id}/body")
        assert response.status_code == 200
        assert response.json() == {"body": "biped", "legs": 2, "arms": 2, "up": "Z"}


def test_entry_without_a_skeleton_has_no_body_and_no_walk(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=False)
        body_response = client.get(f"/v1/library/{source.entry_id}/body")
        assert_error(body_response, 409, "no_skeleton")
        walk_response = client.post(f"/v1/library/{source.entry_id}/walk", json={"body": "biped"})
        assert_error(walk_response, 409, "no_skeleton")
        assert len(client.get("/v1/library").json()) == 1


def test_unknown_entry_is_not_found(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        body_response = client.get("/v1/library/missing-1/body")
        assert_error(body_response, 404, "entry_not_found")
        walk_response = client.post("/v1/library/missing-1/walk", json={"body": "biped"})
        assert_error(walk_response, 404, "entry_not_found")


def test_failed_detection_reports_the_tool_error(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=True)
        set_mode(stubs, "fail")
        response = client.get(f"/v1/library/{source.entry_id}/body")
        error = assert_error(response, 500, "detect_failed")
        assert error["message"] == "No legs found to animate"


def test_walk_creates_a_walk_entry_that_succeeds(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=True)
        response = client.post(f"/v1/library/{source.entry_id}/walk", json={"body": "biped"})
        assert response.status_code == 202
        body = response.json()
        assert body["name"] == "hero (walk)"
        assert body["outcome"] == "running"
        assert body["source_entry_id"] == source.entry_id
        new_id = body["entry_id"]
        final = wait_for(client, f"/v1/library/{new_id}", lambda r: r.json()["outcome"] != "running")
        data = final.json()
        assert data["outcome"] == "succeeded"
        assert data["stats"] == {"vertices": 10, "faces": 20, "textured": True, "duration_s": 1.5}
        assert data["seed"] == 7
        assert data["error"] is None
        assert "model.glb" in data["files"]
        assert client.get(f"/v1/library/{new_id}/files/model.glb").content == b"walk-glb"
        assert len(client.get("/v1/library").json()) == 2


def test_walk_leaves_the_source_entry_untouched(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=True)
        entry_path = lib.folder(source.entry_id) / "entry.json"
        entry_before = entry_path.read_bytes()
        model_before = client.get(f"/v1/library/{source.entry_id}/files/model.glb").content
        response = client.post(f"/v1/library/{source.entry_id}/walk", json={"body": "biped"})
        assert response.status_code == 202
        new_id = response.json()["entry_id"]
        wait_for(client, f"/v1/library/{new_id}", lambda r: r.json()["outcome"] != "running")
        assert entry_path.read_bytes() == entry_before
        assert client.get(f"/v1/library/{source.entry_id}/files/model.glb").content == model_before


def test_failed_walk_ends_failed_with_the_tool_reason(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=True)
        set_mode(stubs, "fail")
        response = client.post(f"/v1/library/{source.entry_id}/walk", json={"body": "quadruped"})
        assert response.status_code == 202
        new_id = response.json()["entry_id"]
        final = wait_for(client, f"/v1/library/{new_id}", lambda r: r.json()["outcome"] != "running")
        data = final.json()
        assert data["outcome"] == "failed"
        assert data["error"] == {"code": "walk_failed", "message": "No legs found to animate"}
        assert "model.glb" not in data["files"]


def test_invalid_body_is_rejected(tmp_path):
    client, lib, stubs = make_tool_client(tmp_path)
    with client:
        source = add_model_entry(lib, tmp_path, "hero", rig=True)
        response = client.post(f"/v1/library/{source.entry_id}/walk", json={"body": "fish"})
        assert_error(response, 422, "invalid_body")
        assert len(client.get("/v1/library").json()) == 1
