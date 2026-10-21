import time

import pytest
from fastapi.testclient import TestClient

import model3d_engine.__main__ as entry
from model3d_engine.__main__ import build_engine, main
from model3d_engine.config import Settings
from model3d_engine.gpu.topology import parse_nvidia_smi

from .test_api import post_job

REAL = parse_nvidia_smi(
    "00000000:25:00.0, NVIDIA GeForce RTX 5070, 2, 2, 12227\n"
    "00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227\n"
)
STUB = "tests.worker_stubs:EchoStage"


def test_main_rejects_non_loopback_host(monkeypatch, capsys):
    monkeypatch.setenv("MODEL3D_HOST", "0.0.0.0")
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 2
    assert "MODEL3D_HOST must be a loopback address" in capsys.readouterr().err


def test_main_runs_uvicorn_on_loopback(monkeypatch):
    calls = {}
    monkeypatch.delenv("MODEL3D_HOST", raising=False)
    monkeypatch.setenv("MODEL3D_PORT", "9001")
    monkeypatch.setattr(entry, "build_engine", lambda settings: "app")
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, host, port: calls.update(app=app, host=host, port=port))
    main()
    assert calls == {"app": "app", "host": "127.0.0.1", "port": 9001}


def test_build_engine_with_fake_factories_serves_health_and_jobs(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    memory = {"0000:25:00.0": (100, 12227), "0000:29:00.0": (200, 12227)}
    app = build_engine(settings, links=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=lambda: memory)
    with TestClient(app) as client:
        deadline = time.monotonic() + 30
        while not client.get("/v1/health").json()["ready"]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert client.get("/v1/health").json() == {
            "ready": True,
            "gpus": [
                {"bus_id": "0000:25:00.0", "stage": "shape", "ready": True, "memory_used_mib": 100, "memory_total_mib": 12227},
                {"bus_id": "0000:29:00.0", "stage": "paint", "ready": True, "memory_used_mib": 200, "memory_total_mib": 12227},
            ],
        }
        job_id = post_job(client, options={"texture": False}).json()["job_id"]
        deadline = time.monotonic() + 30
        while client.get(f"/v1/jobs/{job_id}").json()["state"] != "succeeded":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert client.get(f"/v1/jobs/{job_id}/result").content[:4] == b"glTF"
    assert (tmp_path / "jobs" / job_id / "model.glb").exists()


def test_health_survives_nvidia_smi_failure(tmp_path):
    """Review finding 4: a failing memory reader yields null memory, not a 500."""
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")

    def broken_reader():
        raise FileNotFoundError("nvidia-smi")

    app = build_engine(settings, links=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=broken_reader)
    with TestClient(app) as client:
        deadline = time.monotonic() + 30
        while not (health := client.get("/v1/health")).json()["ready"]:
            assert health.status_code == 200
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert [gpu["memory_used_mib"] for gpu in health.json()["gpus"]] == [None, None]
