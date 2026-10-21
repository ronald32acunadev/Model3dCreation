import pytest

from model3d_engine.gpu.topology import GpuLink


def test_unknown_pcie_generation_raises():
    with pytest.raises(ValueError):
        GpuLink("0000:25:00.0", "x", 6, 16, 12227).bandwidth_gbps
