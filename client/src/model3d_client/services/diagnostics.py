"""Checks that explain why the engine cannot run on this machine or setup."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from model3d_contracts.health import Health

from model3d_client.paths import AppPaths
from model3d_client.services.supervisor import EngineState

WEIGHT_REPOS = ("models--tencent--Hunyuan3D-2.1", "models--facebook--dinov2-giant")


class CheckId(str, Enum):
    gpu = "gpu"
    engine_env = "engine_env"
    hunyuan = "hunyuan"
    weights = "weights"
    engine = "engine"


@dataclass
class Check:
    id: CheckId
    ok: bool
    detail: str  # raw data (GPU names, missing paths, engine message); the view adds translated text


def _run(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout


def run_checks(paths: AppPaths, run: Callable[[list[str]], str] = _run) -> list[Check]:
    """Machine and setup checks; blocking (nvidia-smi), so callers run it off the UI thread."""
    return [_gpu_check(run), _path_check(CheckId.engine_env, [paths.engine_python]),
            _path_check(CheckId.hunyuan, [paths.hunyuan / "hy3dshape"]),
            _path_check(CheckId.weights, [paths.hf / "hub" / repo for repo in WEIGHT_REPOS])]


def _gpu_check(run: Callable[[list[str]], str]) -> Check:
    try:
        output = run(["nvidia-smi", "-L"])
    except FileNotFoundError:
        return Check(CheckId.gpu, False, "nvidia-smi not found")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        return Check(CheckId.gpu, False, str(getattr(error, "output", "") or error).strip())
    gpus = [line.split(" (UUID")[0] for line in output.splitlines() if line.startswith("GPU ")]
    return Check(CheckId.gpu, bool(gpus), "\n".join(gpus) or output.strip())


def _path_check(check_id: CheckId, required: list) -> Check:
    missing = [str(path) for path in required if not path.exists()]
    return Check(check_id, not missing, "\n".join(missing))


def engine_check(state: EngineState, health: Health | None, failure: str | None = None) -> Check:
    """The engine's own report: its health error, or why the process the app started is gone."""
    if state == EngineState.ready:
        return Check(CheckId.engine, True, "")
    if health is not None and health.error is not None:
        return Check(CheckId.engine, False, health.error.message)
    return Check(CheckId.engine, False, failure or state.value)
