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


MODE = {"name": "dual_gpu", "texture": True, "reason": "Two GPUs: shape on 0000:25:00.0, paint on 0000:29:00.0"}


def make_client(
    tmp_path,
    max_image_bytes: int = 1024 * 1024,
    paint=None,
    shape=None,
    wait_ready=True,
    allowed_origins=(),
    texture_available=True,
    rig=None,
    max_model_bytes: int = 1024 * 1024,
) -> TestClient:
    settings = Settings(
        home=tmp_path,
        hunyuan_dir=tmp_path / "hy",
        max_image_bytes=max_image_bytes,
        allowed_origins=allowed_origins,
        max_model_bytes=max_model_bytes,
    )
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, shape or FakeRunner("shape.glb"), paint or FakeRunner("model.glb"), rig=rig)
    reason = None if rig is not None else "UniRig is not installed in vendor/UniRig (see docs/setup.md)"
    app = create_app(
        settings,
        scheduler,
        store,
        bus,
        health=lambda: {"ready": scheduler.ready, "mode": MODE, "gpus": [], "rig": scheduler.rig_available, "rig_reason": reason},
        texture_available=texture_available,
        rig_reason=lambda: reason,
    )
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


def test_model_load_failure_fails_the_job_and_health_stays_ready(tmp_path):
    with make_client(tmp_path, shape=FailingStart("shape.glb")) as client:
        health = client.get("/v1/health").json()
        assert (health["ready"], health["error"]) == (True, None)
        job_id = post_job(client, options={"texture": False}).json()["job_id"]
        body = wait_state(client, job_id, "failed")
        assert body["error"] == {
            "code": "worker_start_failed",
            "message": "shape worker exited with code 1 while loading",
        }
        assert client.get("/v1/health").json()["ready"] is True


def test_api_answers_while_a_model_loads(tmp_path):
    """A slow model load delays the job that needs it, not the port or the submissions."""
    with make_client(tmp_path, shape=SlowStart("shape.glb")) as client:
        assert client.get("/v1/health").json()["ready"] is True
        job_id = post_job(client, options={"texture": False}).json()["job_id"]
        wait_state(client, job_id, "succeeded", timeout=10.0)


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
        assert client.get("/v1/health").json() == {
            "ready": True,
            "mode": MODE,
            "gpus": [],
            "error": None,
            "rig": False,
            "rig_reason": "UniRig is not installed in vendor/UniRig (see docs/setup.md)",
        }


def test_shape_only_refuses_texture(tmp_path):
    with make_client(tmp_path, texture_available=False) as client:
        error = assert_error(post_job(client), 400, "texture_unavailable")
        assert error["message"] == "This engine runs without the paint stage: send \"texture\": false"
        assert_error(post_job(client, b"not an image"), 400, "invalid_image")  # input checks come first
        assert post_job(client, options={"texture": False}).status_code == 202


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


def glb_bytes(textured: bool) -> bytes:
    import numpy
    import trimesh

    mesh = trimesh.creation.box()
    if textured:
        uv = numpy.zeros((len(mesh.vertices), 2))
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.new("RGB", (4, 4), (255, 0, 0)))
    return mesh.export(file_type="glb")


def post_model(client, data: bytes | None):
    files = {"model": ("model.glb", data, "model/gltf-binary")} if data is not None else {}
    return client.post("/v1/rig-jobs", files=files)


def test_glb_has_images():
    from model3d_engine.api.app import glb_has_images

    assert glb_has_images(glb_bytes(textured=True)) is True
    assert glb_has_images(glb_bytes(textured=False)) is False


def test_rig_job_accepts_glb_and_reports_rigging(tmp_path):
    with make_client(tmp_path, rig=FakeRunner("model.glb")) as client:
        response = post_model(client, glb_bytes(textured=True))
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        wait_state(client, job_id, "succeeded")
        assert client.get(f"/v1/jobs/{job_id}/result").content[:4] == b"glTF"


def test_rig_job_rejects_non_glb(tmp_path):
    with make_client(tmp_path, rig=FakeRunner("model.glb")) as client:
        assert_error(post_model(client, image_bytes()), 400, "invalid_model")
        assert_error(post_model(client, None), 400, "invalid_model")


def test_rig_job_rejects_truncated_glb(tmp_path):
    with make_client(tmp_path, rig=FakeRunner("model.glb")) as client:
        assert_error(post_model(client, glb_bytes(textured=False)[:40]), 400, "invalid_model")


def test_rig_job_rejects_oversized_model(tmp_path):
    with make_client(tmp_path, rig=FakeRunner("model.glb"), max_model_bytes=100) as client:
        assert_error(post_model(client, glb_bytes(textured=False)), 413, "model_too_large")


def test_rig_option_rejected_when_rig_unavailable(tmp_path):
    with make_client(tmp_path) as client:
        error = assert_error(post_job(client, options={"rig": True}), 400, "rig_unavailable")
        assert error["message"] == "UniRig is not installed in vendor/UniRig (see docs/setup.md)"
        assert_error(post_model(client, glb_bytes(textured=False)), 400, "rig_unavailable")


def test_health_reports_rig_availability(tmp_path):
    with make_client(tmp_path, rig=FakeRunner("model.glb")) as client:
        health = client.get("/v1/health").json()
        assert (health["rig"], health["rig_reason"]) == (True, None)
