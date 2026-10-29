"""Setup requirement checks: driver, GPU, VRAM, CUDA toolkit, compilers, and the install folder."""

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DRIVER_MIN = 580
MIN_CAPABILITY = (7, 5)  # oldest compute capability in PyTorch's cu130 builds
# Shape and paint peaks plus the 512 MiB margin, from engine/src/model3d_engine/gpu/modes.py.
SHAPE_ONLY_MIB = 9469
PAINT_MIB = 10680
GPU_QUERY = ["nvidia-smi", "--query-gpu=driver_version,compute_cap,memory.total", "--format=csv,noheader,nounits"]
PROBE = ".model3d-probe"


class RequirementId(str, Enum):
    driver = "driver"
    gpu = "gpu"
    vram = "vram"
    cuda_toolkit = "cuda_toolkit"
    compiler = "compiler"


@dataclass
class Requirement:
    id: RequirementId
    ok: bool
    detail: str  # raw data (versions, paths); the view adds translated text
    required: bool = True


@dataclass(frozen=True)
class Toolchain:
    nvcc: Path
    cuda_home: Path
    cuda_version: tuple[int, int]
    arch_list: str
    max_jobs: int


@dataclass
class FolderCheck:
    ok: bool
    problem: str | None  # not_writable, no_space, no_symlinks, no_exec
    free_bytes: int
    needed_bytes: int


def run_command(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout


def default_cuda_dirs() -> list[Path]:
    return sorted(Path("/usr/local").glob("cuda-13*"), reverse=True) + [Path("/usr/local/cuda")]


def _failure_text(error: Exception) -> str:
    return str(getattr(error, "output", "") or error).strip()


def _gpus(output: str) -> list[tuple[str, tuple[int, int], int]]:
    gpus = []
    for line in output.splitlines():
        if line.strip():
            driver, capability, total = (part.strip() for part in line.split(","))
            major, minor = capability.split(".")
            gpus.append((driver, (int(major), int(minor)), int(total)))
    return gpus


def _nvcc_candidates(env: Mapping[str, str], cuda_dirs: list[Path]) -> list[Path]:
    candidates = []
    if env.get("CUDA_HOME"):
        candidates.append(Path(env["CUDA_HOME"]) / "bin/nvcc")
    if env.get("PATH") and (found := shutil.which("nvcc", path=env["PATH"])):
        candidates.append(Path(found))
    candidates += [directory / "bin/nvcc" for directory in cuda_dirs]
    return list(dict.fromkeys(candidates))


def _find_nvcc(run: Callable[[list[str]], str], candidates: list[Path]) -> tuple[Requirement, Path | None, tuple[int, int] | None]:
    """The first nvcc reporting CUDA 13.x; otherwise why none fits."""
    first_other = None
    for path in candidates:
        try:
            match = re.search(r"release (\d+)\.(\d+)", run([str(path), "--version"]))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)))
        if version[0] == 13:
            return Requirement(RequirementId.cuda_toolkit, True, f"{version[0]}.{version[1]} at {path}"), path, version
        first_other = first_other or f"CUDA {version[0]}.{version[1]} at {path} (needs 13.x)"
    return Requirement(RequirementId.cuda_toolkit, False, first_other or "nvcc not found"), None, None


def check_requirements(
    run: Callable[[list[str]], str],
    env: Mapping[str, str],
    cpu_count: int,
    ram_gib: float,
    cuda_dirs: list[Path] | None = None,
) -> tuple[list[Requirement], Toolchain | None]:
    """Every requirement, in display order, and the toolchain to build with when one is usable."""
    try:
        gpus = _gpus(run(GPU_QUERY))
        problem = None if gpus else "no GPU found"
    except FileNotFoundError:
        gpus, problem = [], "nvidia-smi not found"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as error:
        gpus, problem = [], _failure_text(error)

    if problem is not None:
        hardware = [Requirement(requirement, False, problem)
                    for requirement in (RequirementId.driver, RequirementId.gpu, RequirementId.vram)]
    else:
        driver = gpus[0][0]
        driver_ok = int(driver.split(".")[0]) >= DRIVER_MIN
        capabilities = [capability for _, capability, _ in gpus]
        largest = max(total for _, _, total in gpus)
        if largest >= PAINT_MIB:
            vram = "paint_and_shape"
        elif largest >= SHAPE_ONLY_MIB:
            vram = "shape_only"
        else:
            vram = f"{largest} MiB"
        hardware = [
            Requirement(RequirementId.driver, driver_ok, driver if driver_ok else f"{driver} (needs {DRIVER_MIN} or later)"),
            Requirement(RequirementId.gpu, any(c >= MIN_CAPABILITY for c in capabilities),
                        ", ".join(f"{major}.{minor}" for major, minor in capabilities)),
            # Total memory decides: free VRAM changes with whatever else is open right now.
            Requirement(RequirementId.vram, largest >= SHAPE_ONLY_MIB, vram),
        ]

    dirs = default_cuda_dirs() if cuda_dirs is None else cuda_dirs
    toolkit, nvcc, version = _find_nvcc(run, _nvcc_candidates(env, dirs))

    missing = []
    for compiler in ("gcc", "g++"):
        try:
            run([compiler, "--version"])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            missing.append(compiler)
    compilers = Requirement(RequirementId.compiler, not missing, ", ".join(missing) + " not found" if missing else "")

    toolchain = None
    if nvcc is not None and version is not None and gpus:
        major, minor = max(capability for _, capability, _ in gpus)
        toolchain = Toolchain(nvcc=nvcc, cuda_home=nvcc.parent.parent, cuda_version=version,
                              arch_list=f"{major}.{minor}", max_jobs=max(1, min(cpu_count, int(ram_gib // 4))))
    return [*hardware, toolkit, compilers], toolchain


def check_folder(folder: Path, needed_bytes: int) -> FolderCheck:
    """Whether the folder can hold the install: writable, room enough, symlinks and executables work."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return FolderCheck(False, "not_writable", 0, needed_bytes)
    free = shutil.disk_usage(folder).free
    probe, link = folder / PROBE, folder / (PROBE + "-link")
    problem = None
    try:
        try:
            probe.write_text("#!/bin/sh\n")
        except OSError:
            problem = "not_writable"
        if problem is None:
            try:
                os.symlink(probe, link)
            except OSError:
                problem = "no_symlinks"
        if problem is None:
            probe.chmod(0o755)
            if not os.access(probe, os.X_OK):
                problem = "no_exec"
    finally:
        for path in (link, probe):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    if problem is None and free < needed_bytes:
        problem = "no_space"
    return FolderCheck(problem is None, problem, free, needed_bytes)
