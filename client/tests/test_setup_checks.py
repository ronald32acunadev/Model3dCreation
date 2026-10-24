import os
from pathlib import Path

import pytest

from model3d_client.setup import checks
from model3d_client.setup.checks import GPU_QUERY, RequirementId, check_folder, check_requirements

GPU = " ".join(GPU_QUERY)
NVCC13 = "Cuda compilation tools, release 13.1, V13.1.115"
NVCC12 = "Cuda compilation tools, release 12.4, V12.4.131"
CUDA13 = Path("/usr/local/cuda-13.1")
CUDA12 = Path("/usr/local/cuda-12.4")


def fake_run(outputs: dict[str, str | Exception]):
    def run(command: list[str]) -> str:
        key = " ".join(command)
        result = outputs[key] if key in outputs else outputs.get(command[0], FileNotFoundError(key))
        if isinstance(result, Exception):
            raise result
        return result

    return run


def outputs(**overrides) -> dict:
    base = {
        GPU: "595.99.02, 12.0, 12227\n595.99.02, 12.0, 12227\n",
        f"{CUDA13}/bin/nvcc --version": NVCC13,
        "gcc --version": "gcc 15",
        "g++ --version": "g++ 15",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def check(out: dict, env=None, cuda_dirs=(CUDA13,), ram_gib=76.0):
    reqs, toolchain = check_requirements(fake_run(out), env or {}, 16, ram_gib, list(cuda_dirs))
    return {req.id: req for req in reqs}, toolchain


def test_driver_below_580_fails():
    reqs, _ = check(outputs(**{GPU: "575.10, 12.0, 12227"}))
    assert not reqs[RequirementId.driver].ok
    assert reqs[RequirementId.driver].detail == "575.10 (needs 580 or later)"


def test_driver_595_passes():
    reqs, _ = check(outputs())
    assert reqs[RequirementId.driver].ok
    assert reqs[RequirementId.driver].detail == "595.99.02"


def test_no_nvidia_smi_fails_driver_gpu_and_vram():
    reqs, toolchain = check(outputs(**{GPU: None}))
    for requirement in (RequirementId.driver, RequirementId.gpu, RequirementId.vram):
        assert not reqs[requirement].ok
        assert reqs[requirement].detail == "nvidia-smi not found"
    assert toolchain is None


def test_compute_capability_6_1_is_unsupported():
    reqs, _ = check(outputs(**{GPU: "595.99.02, 6.1, 12227"}))
    assert not reqs[RequirementId.gpu].ok
    assert reqs[RequirementId.gpu].detail == "6.1"


def test_vram_below_shape_threshold_fails():
    reqs, _ = check(outputs(**{GPU: "595.99.02, 12.0, 8192"}))
    assert not reqs[RequirementId.vram].ok
    assert reqs[RequirementId.vram].detail == "8192 MiB"


def test_vram_fits_shape_only_reports_shape_only():
    reqs, _ = check(outputs(**{GPU: "595.99.02, 12.0, 10000"}))
    assert reqs[RequirementId.vram].ok
    assert reqs[RequirementId.vram].detail == "shape_only"


def test_no_nvcc_fails_cuda_toolkit():
    reqs, toolchain = check(outputs(**{f"{CUDA13}/bin/nvcc --version": None}), cuda_dirs=())
    assert not reqs[RequirementId.cuda_toolkit].ok
    assert reqs[RequirementId.cuda_toolkit].detail == "nvcc not found"
    assert toolchain is None


def test_cuda_12_nvcc_fails():
    out = outputs(**{f"{CUDA13}/bin/nvcc --version": None, f"{CUDA12}/bin/nvcc --version": NVCC12})
    reqs, toolchain = check(out, cuda_dirs=(CUDA12,))
    assert not reqs[RequirementId.cuda_toolkit].ok
    assert "12.4" in reqs[RequirementId.cuda_toolkit].detail
    assert toolchain is None


def test_cuda_home_12_but_13_installed_picks_13():
    out = outputs(**{f"{CUDA12}/bin/nvcc --version": NVCC12})
    reqs, toolchain = check(out, env={"CUDA_HOME": str(CUDA12)})
    assert reqs[RequirementId.cuda_toolkit].ok
    assert reqs[RequirementId.cuda_toolkit].detail == f"13.1 at {CUDA13}/bin/nvcc"
    assert toolchain.cuda_home == CUDA13


def test_toolchain_arch_and_max_jobs():
    out = outputs(**{GPU: "595.99.02, 12.0, 12227\n595.99.02, 8.6, 24576\n"})
    _, toolchain = check(out)
    assert toolchain.arch_list == "12.0"
    assert toolchain.max_jobs == 16
    assert toolchain.cuda_version == (13, 1)
    assert check(out, ram_gib=20.0)[1].max_jobs == 5


def test_missing_gpp_fails_compiler():
    reqs, _ = check(outputs(**{"g++ --version": None}))
    assert not reqs[RequirementId.compiler].ok
    assert reqs[RequirementId.compiler].detail == "g++ not found"


def test_folder_without_space(tmp_path):
    result = check_folder(tmp_path / "m3d", 10**18)
    assert not result.ok
    assert result.problem == "no_space"


def test_folder_not_writable(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        result = check_folder(parent / "m3d", 1)
    finally:
        parent.chmod(0o700)
    assert not result.ok
    assert result.problem == "not_writable"


def test_folder_ok_cleans_its_probe_files(tmp_path):
    folder = tmp_path / "Disco 2" / "Modelos 3D"
    result = check_folder(folder, 1)
    assert result.ok
    assert result.free_bytes > 0
    assert list(folder.iterdir()) == []


def test_folder_no_symlinks(tmp_path, monkeypatch):
    def no_symlink(*_args, **_kwargs):
        raise OSError("Operation not permitted")

    monkeypatch.setattr(checks.os, "symlink", no_symlink)
    result = check_folder(tmp_path / "m3d", 1)
    assert not result.ok
    assert result.problem == "no_symlinks"
    assert list((tmp_path / "m3d").iterdir()) == []
