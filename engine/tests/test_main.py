import json
import time

import pytest
from fastapi.testclient import TestClient

import model3d_engine.__main__ as entry
from model3d_engine.__main__ import build_engine, main
from model3d_engine.config import Settings
from model3d_engine.gpu.probe import GpuInfo, Inventory

from .fakes import FakeHeadPass, FakeRunner
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


def test_main_serves_on_loopback_and_writes_engine_info(monkeypatch, tmp_path):
    served = {}

    class FakeServer:
        def __init__(self, config):
            served["app"] = config.app

        def run(self, sockets):
            served["hosts"] = [sock.getsockname()[0] for sock in sockets]
            served["port"] = sockets[0].getsockname()[1]
            for sock in sockets:
                sock.close()

    monkeypatch.delenv("MODEL3D_HOST", raising=False)
    monkeypatch.delenv("MODEL3D_LAN_HOST", raising=False)
    monkeypatch.delenv("MODEL3D_API_KEYS", raising=False)
    monkeypatch.setenv("MODEL3D_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL3D_PORT", "auto")
    monkeypatch.setattr(entry, "build_engine", lambda settings: "app")
    monkeypatch.setattr(entry.uvicorn, "Server", FakeServer)
    main()
    assert served["app"] == "app"
    assert served["hosts"] == ["127.0.0.1"]
    assert served["port"] != 0
    assert json.loads((tmp_path / "engine.json").read_text()) == {"port": served["port"], "hosts": ["127.0.0.1"]}


def test_main_refuses_lan_without_keys(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MODEL3D_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL3D_PORT", "auto")
    monkeypatch.setenv("MODEL3D_LAN_HOST", "192.168.90.10")
    monkeypatch.delenv("MODEL3D_API_KEYS", raising=False)
    monkeypatch.delenv("MODEL3D_HOST", raising=False)

    def bind_must_not_run(hosts, port):
        raise AssertionError("bind_sockets must not run without API keys")

    monkeypatch.setattr(entry, "bind_sockets", bind_must_not_run)
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 2
    assert capsys.readouterr().err.strip() == (
        "MODEL3D_LAN_HOST needs MODEL3D_API_KEYS: clients on the network must send a key"
    )


def test_main_exits_when_lan_address_is_missing(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MODEL3D_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL3D_PORT", "auto")
    monkeypatch.setenv("MODEL3D_LAN_HOST", "192.0.2.1")
    monkeypatch.setenv("MODEL3D_API_KEYS", "k1")
    built = []
    monkeypatch.setattr(entry, "build_engine", lambda settings: built.append(settings))
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 2
    assert "192.0.2.1 is not an address of this machine" in capsys.readouterr().err
    assert built == []


def test_build_engine_dual_gpu_health(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", unirig_dir=tmp_path / "UniRig", qwen_tts_dir=tmp_path / "Qwen")
    memory = {"0000:25:00.0": (100, 12227), "0000:29:00.0": (200, 12227)}
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=lambda: memory)
    with TestClient(app, base_url=BASE_URL) as client:
        assert wait_ready(client) == {
            "ready": True,
            "mode": {"name": "dual_gpu", "texture": True, "reason": "Two GPUs: shape on 0000:29:00.0, paint on 0000:25:00.0"},
            "gpus": [
                {"bus_id": "0000:25:00.0", "stage": "paint", "ready": False, "memory_used_mib": 100, "memory_total_mib": 12227, "memory_free_mib": 11761},
                {"bus_id": "0000:29:00.0", "stage": "shape", "ready": False, "memory_used_mib": 200, "memory_total_mib": 12227, "memory_free_mib": 11173},
            ],
            "error": None,
            "rig": False,
            "rig_reason": "UniRig is not installed in vendor/UniRig (see docs/setup.md)",
            "detailed_face": False,
            "detailed_face_reason": "Detailed face needs MODEL3D_GENERATOR=trellis2",
            "voice": False,
            "voice_reason": f"Qwen3-TTS is not installed in {tmp_path / 'Qwen'}",
            "sharper_texture": False,
            "sharper_texture_reason": "Sharper texture needs MODEL3D_GENERATOR=trellis2",
        }
        job_id = run_job(client, {"texture": False})
        assert client.get(f"/v1/jobs/{job_id}/result").content[:4] == b"glTF"
    assert (tmp_path / "jobs" / job_id / "model.glb").exists()


def test_idle_engine_reports_ready_with_nothing_loaded(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", unirig_dir=tmp_path / "UniRig")
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
        assert health["ready"] is True
        assert [gpu["ready"] for gpu in health["gpus"]] == [False, False]


def test_idle_engine_accepts_a_job(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", unirig_dir=tmp_path / "UniRig")
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        wait_ready(client)
        job_id = run_job(client, {"texture": False})
        assert client.get(f"/v1/jobs/{job_id}/result").content[:4] == b"glTF"


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
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", stage_timeout_s=42.0, qwen_tts_dir=tmp_path / "Qwen")
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


def test_build_engine_wires_rig_on_paint_card(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", unirig_dir=tmp_path / "UniRig", qwen_tts_dir=tmp_path / "Qwen")
    memory = {"0000:25:00.0": (4000, 12227), "0000:29:00.0": (200, 12227)}
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=lambda: memory)
    rig = app.state.rig
    assert rig.device == "cuda:0"  # paint sits on 0000:25:00.0, the card with the most free VRAM
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
        assert health["rig"] is False
        assert health["rig_reason"] == "UniRig is not installed in vendor/UniRig (see docs/setup.md)"


def test_build_engine_unsupported_has_no_rig(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    app = build_engine(settings, inventory=NO_GPU, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    assert app.state.rig is None
    with TestClient(app, base_url=BASE_URL) as client:
        health = client.get("/v1/health").json()
        assert (health["rig"], health["rig_reason"]) == (False, "Rigging needs a usable GPU")


def test_build_engine_trellis2_splits_across_two_cards(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", generator="trellis2", trellis_dir=tmp_path / "t2")
    app = build_engine(settings, inventory=REAL, generator_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
        assert health["mode"] == {
            "name": "dual_gpu",
            "texture": True,
            "reason": "TRELLIS.2 generates on 0000:29:00.0 and decodes on 0000:25:00.0",
        }
        assert [(gpu["bus_id"], gpu["stage"]) for gpu in health["gpus"]] == [
            ("0000:25:00.0", "paint"),
            ("0000:29:00.0", "shape"),
        ]
        run_job(client, {"texture": False})


def test_build_engine_trellis2_runs_on_one_card(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", generator="trellis2", trellis_dir=tmp_path / "t2")
    # 11761 MiB free clears TRELLIS.2's 10690 + 512 MiB need; ONE_CARD's 11173 MiB does not.
    roomy = Inventory((GpuInfo(0, "0000:25:00.0", RTX, 12227, 11761, (12, 0), True, 1.0),), None)
    app = build_engine(settings, inventory=roomy, generator_factory=STUB, memory_reader=dict)
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
        assert health["mode"] == {
            "name": "single_gpu_resident",
            "texture": True,
            "reason": "TRELLIS.2 generates shape and texture on 0000:25:00.0",
        }
        assert [(gpu["bus_id"], gpu["stage"]) for gpu in health["gpus"]] == [("0000:25:00.0", "shape+paint")]
        run_job(client, {"texture": False})


def test_build_engine_hunyuan_ignores_generator_factory(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", unirig_dir=tmp_path / "UniRig")
    app = build_engine(
        settings,
        inventory=REAL,
        shape_factory=STUB,
        paint_factory=STUB,
        generator_factory="tests.worker_stubs:BrokenStage",
        memory_reader=dict,
    )
    with TestClient(app, base_url=BASE_URL) as client:
        assert wait_ready(client)["mode"]["name"] == "dual_gpu"


def test_single_gpu_resident_uses_the_swap_loop(tmp_path, monkeypatch):
    """Without it, shape and paint evict each other per task on the one card."""
    seen = {}
    real = entry.Scheduler

    def spy(*args, **kwargs):
        seen["swap"] = kwargs["swap"]
        return real(*args, **kwargs)

    monkeypatch.setattr(entry, "Scheduler", spy)
    roomy = Inventory((GpuInfo(0, "0000:29:00.0", RTX, 24576, 20000, (12, 0), True, 15.76),), None)
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", mode="single_gpu_resident")
    build_engine(settings, inventory=roomy, shape_factory=STUB, paint_factory=STUB, memory_reader=dict)
    assert seen["swap"] is True


def test_hunyuan_engine_has_no_detailed_face(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", unirig_dir=tmp_path / "UniRig")
    app = build_engine(settings, inventory=REAL, shape_factory=STUB, paint_factory=STUB, memory_reader=dict, rig_runner=FakeRunner("model.glb"))
    assert app.state.head_pass is None
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
    assert (health["detailed_face"], health["detailed_face_reason"]) == (False, "Detailed face needs MODEL3D_GENERATOR=trellis2")


def test_trellis_engine_with_head_pass_reports_detailed_face(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", generator="trellis2", trellis_dir=tmp_path / "t2")
    app = build_engine(settings, inventory=REAL, generator_factory=STUB, memory_reader=dict, rig_runner=FakeRunner("model.glb"), head_pass=FakeHeadPass(tmp_path))
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
    assert (health["detailed_face"], health["detailed_face_reason"]) == (True, None)


def test_default_head_pass_uses_the_engine_port(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", generator="trellis2", trellis_dir=tmp_path / "t2", port=9123)
    app = build_engine(settings, inventory=REAL, generator_factory=STUB, memory_reader=dict, rig_runner=FakeRunner("model.glb"))
    assert app.state.head_pass._engine_url == "http://127.0.0.1:9123"


def test_trellis_engine_without_rig_names_the_rig_reason(tmp_path):
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy", generator="trellis2", trellis_dir=tmp_path / "t2", unirig_dir=tmp_path / "UniRig")
    app = build_engine(settings, inventory=REAL, generator_factory=STUB, memory_reader=dict)
    assert app.state.head_pass is None
    with TestClient(app, base_url=BASE_URL) as client:
        health = wait_ready(client)
    assert health["detailed_face_reason"] == "UniRig is not installed in vendor/UniRig (see docs/setup.md)"
    assert health["detailed_face"] is False


def test_main_builds_the_engine_with_the_bound_port(tmp_path, monkeypatch):
    served = {}

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self, sockets):
            for sock in sockets:
                served["port"] = sock.getsockname()[1]
                sock.close()

    built = []

    def fake_build_engine(settings):
        built.append(settings)
        return "app"

    monkeypatch.delenv("MODEL3D_HOST", raising=False)
    monkeypatch.delenv("MODEL3D_LAN_HOST", raising=False)
    monkeypatch.delenv("MODEL3D_API_KEYS", raising=False)
    monkeypatch.setenv("MODEL3D_HOME", str(tmp_path))
    monkeypatch.setenv("MODEL3D_PORT", "auto")
    monkeypatch.setattr(entry, "build_engine", fake_build_engine)
    monkeypatch.setattr(entry.uvicorn, "Server", FakeServer)

    main()

    assert built[0].port == served["port"]
    assert served["port"] != 0


def test_texturing_weights_need_the_shape_encoder(tmp_path):
    from model3d_engine.__main__ import TEXTURING_ENCODER, texturing_weights_reason

    snapshot = tmp_path / "hub/models--microsoft--TRELLIS.2-4B/snapshots/abc"
    (snapshot / "ckpts").mkdir(parents=True)
    assert "texturing weights are missing" in texturing_weights_reason(tmp_path)
    (snapshot / TEXTURING_ENCODER).write_bytes(b"x")
    assert texturing_weights_reason(tmp_path) is None
