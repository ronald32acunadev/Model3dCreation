import pytest

from model3d_engine.config import load_settings


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s = load_settings({}, repo_root=tmp_path)
    assert s.home == tmp_path / "data"
    assert s.host == "127.0.0.1"
    assert s.port == 8765
    assert s.shape_bus is None
    assert s.paint_bus is None
    assert s.mode is None
    assert s.hunyuan_dir == tmp_path / "vendor/Hunyuan3D-2.1"
    assert s.jobs_dir == s.home / "jobs"
    assert s.hf_home == s.home / "hf"
    assert s.max_image_bytes == 20 * 1024 * 1024
    assert s.allowed_origins == ()
    assert s.stage_timeout_s == 900.0


def test_env_overrides(tmp_path):
    env = {
        "MODEL3D_HOME": str(tmp_path / "data"),
        "MODEL3D_PORT": "9000",
        "MODEL3D_SHAPE_BUS": "00000000:2A:00.0",
        "MODEL3D_PAINT_BUS": "0000:29:00.0",
        "MODEL3D_MODE": "single_gpu_swap",
        "MODEL3D_HUNYUAN_DIR": str(tmp_path / "hy"),
        "MODEL3D_ALLOWED_ORIGINS": " http://localhost:5173, HTTPS://Tool.test/ ,",
        "MODEL3D_STAGE_TIMEOUT_S": "1200",
    }
    s = load_settings(env, repo_root=tmp_path)
    assert s.home == tmp_path / "data"
    assert s.port == 9000
    assert s.shape_bus == "0000:2a:00.0"
    assert s.paint_bus == "0000:29:00.0"
    assert s.mode == "single_gpu_swap"
    assert s.hunyuan_dir == tmp_path / "hy"
    assert s.allowed_origins == ("http://localhost:5173", "https://tool.test")
    assert s.stage_timeout_s == 1200.0


def test_mode_and_bus_validation(tmp_path):
    with pytest.raises(ValueError) as raised:
        load_settings({"MODEL3D_SHAPE_BUS": "0000:25:00.0"}, repo_root=tmp_path)
    assert str(raised.value) == "Set both MODEL3D_SHAPE_BUS and MODEL3D_PAINT_BUS, or neither"
    with pytest.raises(ValueError):
        load_settings({"MODEL3D_PAINT_BUS": "0000:29:00.0"}, repo_root=tmp_path)
    for mode in ("unsupported", "fast"):
        with pytest.raises(ValueError) as raised:
            load_settings({"MODEL3D_MODE": mode}, repo_root=tmp_path)
        assert str(raised.value) == (
            f"MODEL3D_MODE must be one of dual_gpu, single_gpu_resident, single_gpu_swap, shape_only; got {mode!r}"
        )


def test_unirig_dir_default_and_override(tmp_path):
    assert load_settings({}, repo_root=tmp_path).unirig_dir == tmp_path / "vendor/UniRig"
    assert load_settings({"MODEL3D_UNIRIG_DIR": str(tmp_path / "rig")}, repo_root=tmp_path).unirig_dir == tmp_path / "rig"
