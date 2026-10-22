import subprocess

import pytest
from model3d_contracts.health import Health

from fake_engine import make_health
from model3d_client.paths import AppPaths
from model3d_client.services.diagnostics import CheckId, engine_check, run_checks
from model3d_client.services.supervisor import EngineState

GPU_LIST = "GPU 0: NVIDIA GeForce RTX 5070 (UUID: GPU-1)\nGPU 1: NVIDIA GeForce RTX 5070 (UUID: GPU-2)\n"


@pytest.fixture
def complete(tmp_path):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    paths.engine_python.parent.mkdir(parents=True)
    paths.engine_python.write_text("")
    (paths.hunyuan / "hy3dshape").mkdir(parents=True)
    (paths.hf / "hub/models--tencent--Hunyuan3D-2.1").mkdir(parents=True)
    (paths.hf / "hub/models--facebook--dinov2-giant").mkdir(parents=True)
    return paths


def by_id(checks):
    return {check.id: check for check in checks}


def test_all_checks_pass_with_complete_tree(complete):
    checks = by_id(run_checks(complete, run=lambda cmd: GPU_LIST))
    assert [c for c in CheckId if c != CheckId.engine] == list(checks)
    assert all(check.ok for check in checks.values())
    assert "RTX 5070" in checks[CheckId.gpu].detail


def test_missing_weights_reported(complete):
    (complete.hf / "hub/models--facebook--dinov2-giant").rmdir()
    checks = by_id(run_checks(complete, run=lambda cmd: GPU_LIST))
    assert not checks[CheckId.weights].ok
    assert "dinov2-giant" in checks[CheckId.weights].detail
    assert checks[CheckId.engine_env].ok


def test_missing_environment_and_clone_reported(tmp_path):
    paths = AppPaths(repo=tmp_path, data=tmp_path / "data")
    checks = by_id(run_checks(paths, run=lambda cmd: GPU_LIST))
    assert not checks[CheckId.engine_env].ok
    assert not checks[CheckId.hunyuan].ok
    assert not checks[CheckId.weights].ok


def test_missing_nvidia_smi_reported(complete):
    def run(cmd):
        raise FileNotFoundError(cmd[0])

    assert not by_id(run_checks(complete, run=run))[CheckId.gpu].ok


def test_nvidia_smi_failure_or_no_gpu_reported(complete):
    def failing(cmd):
        raise subprocess.CalledProcessError(9, cmd, output="NVIDIA-SMI has failed")

    assert not by_id(run_checks(complete, run=failing))[CheckId.gpu].ok
    assert not by_id(run_checks(complete, run=lambda cmd: "No devices were found\n"))[CheckId.gpu].ok


def test_engine_check_carries_health_error():
    health = Health.model_validate(make_health(ready=False, error={"code": "mode_unavailable", "message": "no GPU fits"}))
    check = engine_check(EngineState.error, health)
    assert (check.id, check.ok, check.detail) == (CheckId.engine, False, "no GPU fits")


def test_engine_check_uses_supervisor_failure():
    check = engine_check(EngineState.crashed, None, "The engine exited with code 1")
    assert not check.ok and check.detail == "The engine exited with code 1"
    assert engine_check(EngineState.ready, Health.model_validate(make_health())).ok
