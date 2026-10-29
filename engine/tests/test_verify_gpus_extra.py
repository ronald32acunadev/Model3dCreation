"""Failure paths of scripts/verify_gpus.py found by the phase 1 review."""

import importlib.util
import pathlib
import subprocess
import sys
from types import SimpleNamespace

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_gpus", REPO_ROOT / "scripts" / "verify_gpus.py")
verify_gpus = importlib.util.module_from_spec(spec)
sys.modules["verify_gpus"] = verify_gpus
spec.loader.exec_module(verify_gpus)

REAL_LINES = (
    "00000000:25:00.0, NVIDIA GeForce RTX 5070, 2, 2, 12227\n"
    "00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227\n"
)


def test_wrong_gpu_count_keeps_links(monkeypatch):
    stdout = REAL_LINES + "00000000:2B:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227\n"
    monkeypatch.setattr(
        verify_gpus.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    )
    check, links, placement = verify_gpus._check_nvidia_smi()
    assert check.status == "fail"
    assert "expected 2 GPUs, found 3" in check.detail
    assert len(links) == 3
    assert placement is None


def test_nvcc_major_mismatch_fails(monkeypatch):
    monkeypatch.setattr(verify_gpus, "_find_nvcc", lambda: pathlib.Path("/fake/nvcc"))
    monkeypatch.setattr(
        verify_gpus.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, stdout="Cuda compilation tools, release 12.8, V12.8.93\n", stderr=""
        ),
    )
    check = verify_gpus._check_nvcc("13.0", compare=True)
    assert check.status == "fail"
    assert "13.0" in check.detail
    assert "12.8" in check.detail


def test_device_props_error_is_a_failed_check(monkeypatch):
    def broken_props(index):
        raise RuntimeError("driver exploded")

    fake_torch = SimpleNamespace(
        __version__="2.9.1",
        version=SimpleNamespace(cuda="13.0"),
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_arch_list=lambda: ["sm_120"],
            device_count=lambda: 2,
            get_device_properties=broken_props,
            mem_get_info=lambda i: (11 * 1024**3, 12 * 1024**3),
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(verify_gpus, "_check_nvidia_smi", lambda: (verify_gpus.Check("nvidia-smi", "pass", ""), [], None))
    monkeypatch.setattr(verify_gpus, "_check_nvcc", lambda *a, **k: verify_gpus.Check("nvcc", "pass", ""))

    checks, _ = verify_gpus.run_checks(REPO_ROOT)

    for name in ("cuda:0", "cuda:1"):
        check = next(c for c in checks if c.name == name)
        assert check.status == "fail"
        assert "driver exploded" in check.detail
