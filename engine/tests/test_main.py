import time

import pytest
from fastapi.testclient import TestClient

import model3d_engine.__main__ as entry
from model3d_engine.__main__ import build_engine, main
from model3d_engine.config import Settings
from model3d_engine.gpu.probe import GpuInfo, Inventory

from .test_api import post_job

RTX = "NVIDIA GeForce RTX 5070"
# The owner's machine at idle (docs/architecture.md, section 2).
REAL = Inventory(
    (
        GpuInfo(0, "0000:25:00.0", RTX, 12227, 11761, (12, 0), True, 1.0),
        GpuInfo(1, "0000:29:00.0", RTX, 12227, 11173, (12, 0), True, 15.76),
    ),
    None,
)
ONE_CARD = Inventory((GpuInfo(0, "0000:29:00.0", RTX, 12227, 11173, (12, 0), True, 15.76),), None)
NO_GPU = Inventory((), "No NVIDIA GPU is visible to PyTorch")
BASE_URL = "http://127.0.0.1:8765"


def wait_ready(client, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while not (health := client.get("/v1/health").json())["ready"]:
        assert time.monotonic() < deadline, health
        time.sleep(0.05)
    return health


def run_job(client, options: dict) -> str:
    job_id = post_job(client, options=options).json()["job_id"]
    deadline = time.monotonic() + 30
    while (state := client.get(f"/v1/jobs/{job_id}").json()["state"]) != "succeeded":
        assert state not in ("failed", "cancelled"), client.get(f"/v1/jobs/{job_id}").json()
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return job_id
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


def test_build_engine_dual_gpu_health(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    memory = {"0000:25:00.0": (100, 12227), "0000:29:00.0": (200, 12227)}
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=lambda: memory)
    with TestClient(app, base_url=BASE_URL) as client:
        assert wait_ready(client) == {
            "ready": True,
            "mode": {"name": "dual_gpu", "texture": True, "reason": "Two GPUs: shape on 0000:29:00.0, paint on 0000:25:00.0"},
            "gpus": [
                {"bus_id": "0000:25:00.0", "stage": "paint", "ready": True, "memory_used_mib": 100, "memory_total_mib": 12227, "memory_free_mib": 11761},
                {"bus_id": "0000:29:00.0", "stage": "shape", "ready": True, "memory_used_mib": 200, "memory_total_mib": 12227, "memory_free_mib": 11173},
            ],
            "error": None,
            "rig": False,
            "rig_reason": None,
        }
        job_id = run_job(client, {"texture": False})
        assert client.get(f"/v1/jobs/{job_id}/result").content[:4] == b"glTF"
    assert (tmp_path / "jobs" / job_id / "model.glb").exists()


def test_build_engine_swap_mode_runs_a_textured_job(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    app = build_engine(settings, inventory=ONE_CARD, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
        assert health["mode"]["name"] == "single_gpu_swap"
        assert [(gpu["bus_id"], gpu["stage"]) for gpu in health["gpus"]] == [("0000:29:00.0", "shape+paint")]
        run_job(client, {})


def test_unsupported_mode_serves_health(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    app = build_engine(settings, inventory=NO_GPU, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = client.get("/v1/health").json()
        assert health["ready"] is False
        assert health["mode"] == {"name": "unsupported", "texture": False, "reason": "No NVIDIA GPU is visible to PyTorch"}
        assert health["gpus"] == []
        assert health["error"] == {"code": "mode_unavailable", "message": "No NVIDIA GPU is visible to PyTorch"}
        response = post_job(client)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "engine_not_ready"
        assert "No NVIDIA GPU is visible to PyTorch" in response.json()["error"]["message"]


def test_forced_mode_unavailable_is_reported(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", mode="dual_gpu")
    app = build_engine(settings, inventory=ONE_CARD, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = client.get("/v1/health").json()
        assert health["mode"]["name"] == "unsupported"
        assert health["error"] == {"code": "mode_unavailable", "message": "MODEL3D_MODE=dual_gpu needs two GPUs; found 1"}
        assert [gpu["stage"] for gpu in health["gpus"]] == [None]


def test_health_survives_nvidia_smi_failure(tmp_path):
    """Review finding 4: a failing memory reader yields null memory, not a 500."""
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")

    def broken_reader():
        raise FileNotFoundError("nvidia-smi")

    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=broken_reader)
    with TestClient(app, base_url=BASE_URL) as client:
        deadline = time.monotonic() + 30
        while not (health := client.get("/v1/health")).json()["ready"]:
            assert health.status_code == 200
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert [gpu["memory_used_mib"] for gpu in health.json()["gpus"]] == [None, None]


def test_build_engine_passes_stage_timeout(tmp_path, monkeypatch):
    timeouts = []
    real = entry.ProcessStageRunner

    def capture(*args, **kwargs):
        timeouts.append(kwargs.get("task_timeout_s"))
        return real(*args, **kwargs)

    monkeypatch.setattr(entry, "ProcessStageRunner", capture)
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", stage_timeout_s=42.0)
    build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    assert timeouts == [42.0, 42.0]


def test_forced_shape_only_refuses_textured_jobs(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", mode="shape_only")
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
        assert health["mode"]["texture"] is False
        assert [gpu["stage"] for gpu in health["gpus"]] == ["shape", None]  # the card with the most free VRAM
        assert post_job(client).json()["error"]["code"] == "texture_unavailable"
        run_job(client, {"texture": False})


def test_uvicorn_can_serve_websockets():
    # The job events route is a WebSocket; without a WebSocket library uvicorn answers it with 404.
    # TestClient bypasses uvicorn, so only this check catches a missing library.
    import uvicorn

    config = uvicorn.Config(app=lambda scope, receive, send: None, ws="auto")
    config.load()
    assert config.ws_protocol_class is not None
