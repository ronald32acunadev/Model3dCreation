"""Rigging on real GPUs through the full engine. Needs the full environment from docs/setup.md,
UniRig's (optional) install, and free VRAM (unload LM Studio/Ollama models first)."""

import io
import json
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest
import trimesh
from fastapi.testclient import TestClient

from model3d_engine.__main__ import build_engine
from model3d_engine.config import load_settings

PENGUIN = Path(__file__).parent / "assets" / "penguin.png"
BASE_URL = "http://127.0.0.1:8765"

pytestmark = pytest.mark.gpu


def glb_json(data: bytes) -> dict:
    (chunk_length,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20 : 20 + chunk_length])


class VramSampler:
    """Peak nvidia-smi memory.used per GPU index, sampled every 0.5 s."""

    def __init__(self):
        self.peak: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            rows = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True,
            ).stdout.splitlines()
            for row in rows:
                index, used = (part.strip() for part in row.split(","))
                self.peak[index] = max(self.peak.get(index, 0), int(used))
            time.sleep(0.5)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()


@pytest.fixture(scope="module")
def client():
    app = build_engine(load_settings())
    with TestClient(app, base_url=BASE_URL) as client:
        deadline = time.monotonic() + 600
        while not (health := client.get("/v1/health").json())["ready"]:
            assert health["error"] is None, health
            assert time.monotonic() < deadline
            time.sleep(1)
        assert health["rig"] is True, health["rig_reason"]
        yield client


def wait_done(client, job_id: str) -> dict[str, float]:
    """Wait for success; return seconds spent in each stage."""
    stage_started, last = {}, None
    deadline = time.monotonic() + 1800
    while (job := client.get(f"/v1/jobs/{job_id}").json())["state"] not in ("succeeded", "failed", "cancelled"):
        if job["stage"] and job["stage"] != last:
            stage_started[job["stage"]] = time.monotonic()
            last = job["stage"]
        assert time.monotonic() < deadline
        time.sleep(0.5)
    assert job["state"] == "succeeded", job["error"]
    finished = time.monotonic()
    names = list(stage_started)
    ends = [stage_started[name] for name in names[1:]] + [finished]
    return {name: end - stage_started[name] for name, end in zip(names, ends)}


def submit_image(client, options: dict) -> str:
    files = {"image": ("penguin.png", PENGUIN.read_bytes(), "image/png")}
    response = client.post("/v1/jobs", files=files, data={"options": json.dumps(options)})
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def assert_rigged(data: bytes) -> dict:
    document = glb_json(data)
    assert len(document.get("skins", [])) == 1
    assert len(document["skins"][0]["joints"]) >= 10
    return document


def test_penguin_with_skeleton_dual_mode(client):
    assert client.get("/v1/health").json()["mode"]["name"] == "dual_gpu"
    with VramSampler() as vram:
        job_id = submit_image(client, {"rig": True})
        seconds = wait_done(client, job_id)
    document = assert_rigged(client.get(f"/v1/jobs/{job_id}/result").content)
    assert document.get("images")
    print(f"\nstage seconds {seconds}, peak VRAM MiB {vram.peak}")


def test_rig_only_on_finished_model(client):
    job_id = submit_image(client, {})
    wait_done(client, job_id)
    model = client.get(f"/v1/jobs/{job_id}/result").content
    response = client.post("/v1/rig-jobs", files={"model": ("model.glb", model, "model/gltf-binary")})
    assert response.status_code == 202, response.text
    with VramSampler() as vram:
        seconds = wait_done(client, response.json()["job_id"])
    rigged = client.get(f"/v1/jobs/{response.json()['job_id']}/result").content
    assert assert_rigged(rigged).get("images")
    print(f"\nrig only seconds {seconds}, peak VRAM MiB {vram.peak}")


def test_untextured_rig_job_caps_faces(client):
    job_id = submit_image(client, {"texture": False, "rig": True})
    wait_done(client, job_id)
    data = client.get(f"/v1/jobs/{job_id}/result").content
    assert_rigged(data)
    scene = trimesh.load(io.BytesIO(data), file_type="glb", force="scene")
    assert sum(len(geometry.faces) for geometry in scene.geometry.values()) <= 150_000
