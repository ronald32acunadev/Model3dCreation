import pytest

from model3d_engine.gpu.topology import GpuLink, Placement, parse_nvidia_smi, resolve_placement

REAL = parse_nvidia_smi(
    "00000000:25:00.0, NVIDIA GeForce RTX 5070, 2, 2, 12227\n"
    "00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227\n"
)


def test_resolve_placement_real_machine():
    assert resolve_placement(REAL, "0000:25:00.0", "0000:29:00.0") == Placement("cuda:0", "cuda:1", "cpu")
    assert resolve_placement(REAL, "0000:29:00.0", "0000:25:00.0") == Placement("cuda:1", "cuda:0", "cpu")
    assert resolve_placement(list(reversed(REAL)), "0000:25:00.0", "0000:29:00.0") == Placement(
        "cuda:0", "cuda:1", "cpu"
    )


def test_resolve_placement_missing_bus():
    with pytest.raises(ValueError, match="GPU 0000:2a:00.0 not found"):
        resolve_placement(REAL, "0000:2a:00.0", "0000:29:00.0")


def test_resolve_placement_same_bus():
    with pytest.raises(ValueError, match="shape and paint must be different GPUs"):
        resolve_placement(REAL, "0000:25:00.0", "0000:25:00.0")


def test_unknown_pcie_generation_raises():
    with pytest.raises(ValueError):
        GpuLink("0000:25:00.0", "x", 6, 16, 12227).bandwidth_gbps
