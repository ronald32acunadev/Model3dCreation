import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from model3d_engine.api.app import create_app
from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore

from .fakes import FakeRunner


class NeverReady(FakeRunner):
    async def start(self) -> None:
        self.ready = False


def image_bytes(mode: str = "RGBA", fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (8, 8), (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)).save(buffer, fmt)
    return buffer.getvalue()


def make_client(tmp_path, max_image_bytes: int = 1024 * 1024, paint=None, shape=None) -> TestClient:
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", max_image_bytes=max_image_bytes)
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, shape or FakeRunner("shape.glb"), paint or FakeRunner("model.glb"))
    app = create_app(settings, scheduler, store, bus, health=lambda: {"ready": scheduler.ready, "gpus": []})
    return TestClient(app)


def post_job(client, data: bytes | None = None, options: dict | None = None, filename: str = "in.png"):
    files = {"image": (filename, data if data is not None else image_bytes(), "application/octet-stream")}
    form = {"options": json.dumps(options)} if options is not None else {}
    return client.post("/v1/jobs", files=files, data=form)


def wait_state(client, job_id: str, state: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/v1/jobs/{job_id}").json()
        if body["state"] == state:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {state}")


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    return body["error"]


def test_submit_returns_202_and_job(tmp_path):
    with make_client(tmp_path) as client:
        response = post_job(client, options={"seed": 7, "texture": False})
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["state"] == "queued"
        assert job["seed"] == 7
        assert job["queue_position"] == 0
        assert job["links"] == {
            "self": f"/v1/jobs/{job['job_id']}",
            "events": f"/v1/jobs/{job['job_id']}/events",
            "result": f"/v1/jobs/{job['job_id']}/result",
        }
        assert job["created_at"].endswith("Z")


def test_png_with_alpha_and_rgb_jpeg_are_accepted(tmp_path):
    with make_client(tmp_path) as client:
        assert post_job(client, image_bytes("RGBA", "PNG")).status_code == 202
        assert post_job(client, image_bytes("RGB", "JPEG"), filename="in.jpg").status_code == 202


def test_rejects_non_image(tmp_path):
    with make_client(tmp_path) as client:
        assert_error(post_job(client, b"definitely not an image"), 400, "invalid_image")


def test_rejects_gif(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "GIF")
    with make_client(tmp_path) as client:
        assert_error(post_job(client, buffer.getvalue(), filename="in.gif"), 400, "invalid_image")


def test_rejects_missing_image(tmp_path):
    with make_client(tmp_path) as client:
        assert_error(client.post("/v1/jobs", data={"options": "{}"}), 400, "invalid_image")


def test_rejects_oversized_image(tmp_path):
    with make_client(tmp_path, max_image_bytes=10) as client:
        assert_error(post_job(client), 413, "image_too_large")


def test_rejects_bad_options(tmp_path):
    with make_client(tmp_path) as client:
        error = assert_error(post_job(client, options={"target_faces": 5}), 400, "invalid_options")
        assert "target_faces must be at least 1000" in error["message"]
        assert_error(client.post("/v1/jobs", files={"image": ("a.png", image_bytes())}, data={"options": "{oops"}), 400, "invalid_options")


def test_not_ready_returns_503(tmp_path):
    with make_client(tmp_path, shape=NeverReady("shape.glb")) as client:
        assert_error(post_job(client), 503, "engine_not_ready")


def test_result_before_finish_is_409_then_glb_after(tmp_path):
    with make_client(tmp_path, paint=FakeRunner("model.glb", delay=0.3)) as client:
        job_id = post_job(client).json()["job_id"]
        assert_error(client.get(f"/v1/jobs/{job_id}/result"), 409, "job_not_finished")
        wait_state(client, job_id, "succeeded")
        result = client.get(f"/v1/jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.headers["content-type"] == "model/gltf-binary"
        assert result.content[:4] == b"glTF"


def test_unknown_job_is_404(tmp_path):
    with make_client(tmp_path) as client:
        for method, path in (("get", "/v1/jobs/nope"), ("get", "/v1/jobs/nope/result"), ("delete", "/v1/jobs/nope")):
            assert_error(getattr(client, method)(path), 404, "job_not_found")


def test_delete_running_job_cancels_it(tmp_path):
    with make_client(tmp_path, shape=FakeRunner("shape.glb", delay=0.3)) as client:
        job_id = post_job(client).json()["job_id"]
        wait_state(client, job_id, "shaping")
        response = client.delete(f"/v1/jobs/{job_id}")
        assert response.status_code == 202
        assert response.json()["job_id"] == job_id
        wait_state(client, job_id, "cancelled")


def test_delete_finished_job_is_409(tmp_path):
    with make_client(tmp_path) as client:
        job_id = post_job(client, options={"texture": False}).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        assert_error(client.delete(f"/v1/jobs/{job_id}"), 409, "job_finished")
        assert client.get(f"/v1/jobs/{job_id}").json()["state"] == "succeeded"


def test_health_reports_injected_status(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/v1/health").json() == {"ready": True, "gpus": []}
