import asyncio
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
from model3d_engine.stages.protocol import StageError

from .fakes import FakeRunner


class NeverReady(FakeRunner):
    async def start(self) -> None:
        self.ready = False


class FailingStart(FakeRunner):
    async def start(self) -> None:
        raise StageError("worker_start_failed", "shape worker exited with code 1 while loading")


class SlowStart(FakeRunner):
    async def start(self) -> None:
        await asyncio.sleep(0.5)
        self.ready = True


def image_bytes(mode: str = "RGBA", fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (8, 8), (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)).save(buffer, fmt)
    return buffer.getvalue()


class ReadyClient(TestClient):
    """TestClient that waits for the engine to finish loading before the test runs."""

    def __enter__(self):
        client = super().__enter__()
        if self.wait_ready:
            deadline = time.monotonic() + 5
            while not client.get("/v1/health").json()["ready"]:
                assert time.monotonic() < deadline, "engine never became ready"
                time.sleep(0.01)
        return client


def make_client(
    tmp_path, max_image_bytes: int = 1024 * 1024, paint=None, shape=None, wait_ready=True, allowed_origins=()
) -> TestClient:
    settings = Settings(
        home=tmp_path, hunyuan_dir=tmp_path / "hy", max_image_bytes=max_image_bytes, allowed_origins=allowed_origins
    )
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, shape or FakeRunner("shape.glb"), paint or FakeRunner("model.glb"))
    app = create_app(settings, scheduler, store, bus, health=lambda: {"ready": scheduler.ready, "gpus": []})
    client = ReadyClient(app, base_url="http://127.0.0.1:8765")
    client.wait_ready = wait_ready
    return client


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
    with make_client(tmp_path, shape=NeverReady("shape.glb"), wait_ready=False) as client:
        assert_error(post_job(client), 503, "engine_not_ready")


def test_api_answers_while_models_load(tmp_path):
    """Review finding 2: the port answers during startup, with health not ready and 503 on submit."""
    with make_client(tmp_path, shape=SlowStart("shape.glb"), wait_ready=False) as client:
        assert client.get("/v1/health").json()["ready"] is False
        assert_error(post_job(client), 503, "engine_not_ready")
        deadline = time.monotonic() + 5
        while not client.get("/v1/health").json()["ready"]:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert post_job(client).status_code == 202


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
        assert client.get("/v1/health").json() == {"ready": True, "gpus": [], "error": None}


def test_startup_failure_is_reported_in_health(tmp_path):
    """Review advisory: a failed initial load shows in health instead of ready: false forever."""
    with make_client(tmp_path, shape=FailingStart("shape.glb"), wait_ready=False) as client:
        deadline = time.monotonic() + 5
        while (health := client.get("/v1/health").json())["error"] is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert health["ready"] is False
        assert health["error"] == {
            "code": "worker_start_failed",
            "message": "shape worker exited with code 1 while loading",
        }
        error = assert_error(post_job(client), 503, "engine_not_ready")
        assert "shape worker exited with code 1 while loading" in error["message"]


def test_cross_origin_post_and_delete_are_forbidden(tmp_path):
    """Review advisory: a browser page on another origin cannot queue or cancel jobs."""
    with make_client(tmp_path, shape=FakeRunner("shape.glb", delay=0.3)) as client:
        evil = {"Origin": "http://evil.example"}
        files = {"image": ("in.png", image_bytes(), "image/png")}
        assert_error(client.post("/v1/jobs", files=files, headers=evil), 403, "forbidden_origin")
        assert not (tmp_path / "jobs").exists() or not any((tmp_path / "jobs").iterdir())
        job_id = post_job(client).json()["job_id"]
        assert_error(client.delete(f"/v1/jobs/{job_id}", headers={"Origin": "null"}), 403, "forbidden_origin")
        assert client.get(f"/v1/jobs/{job_id}").json()["state"] != "cancelled"


def test_allowed_origin_can_submit(tmp_path):
    with make_client(tmp_path, allowed_origins=("http://localhost:5173",)) as client:
        files = {"image": ("in.png", image_bytes(), "image/png")}
        response = client.post("/v1/jobs", files=files, headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 202, response.text
        other = client.post("/v1/jobs", files=files, headers={"Origin": "http://localhost:3000"})
        assert_error(other, 403, "forbidden_origin")


def test_non_loopback_host_is_forbidden(tmp_path):
    """Review advisory: DNS rebinding sends a foreign Host header to the loopback port."""
    with make_client(tmp_path) as client:
        for host in ("127.0.0.1:8765", "localhost:8765", "LocalHost:8765", "[::1]:8765", "[::1]", "127.0.0.1"):
            assert client.get("/v1/health", headers={"Host": host}).status_code == 200, host
        for host in ("evil.example:8765", "evil.example", "127.0.0.1.evil.example", "", "[evil]:8765"):
            assert_error(client.get("/v1/health", headers={"Host": host}), 403, "forbidden_origin")
        assert_error(post_job_with_host(client, "evil.example:8765"), 403, "forbidden_origin")


def post_job_with_host(client, host: str):
    files = {"image": ("in.png", image_bytes(), "image/png")}
    return client.post("/v1/jobs", files=files, headers={"Host": host})
