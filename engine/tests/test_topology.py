import pytest

from model3d_engine.gpu.topology import GpuLink, parse_nvidia_smi


def test_unknown_pcie_generation_raises():
    with pytest.raises(ValueError):
        GpuLink("0000:25:00.0", "x", 6, 16, 12227).bandwidth_gbps


def test_parse_nvidia_smi_with_display_active():
    text = (
        "00000000:25:00.0, NVIDIA GeForce RTX 5070, 2, 2, 12227, Enabled\n"
        "00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227, Disabled\n"
    )
    links = parse_nvidia_smi(text)
    assert len(links) == 2
    assert links[0].display_active is True
    assert links[1].display_active is False


def test_parse_nvidia_smi_backward_compatibility_5_fields():
    text = "00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227\n"
    links = parse_nvidia_smi(text)
    assert len(links) == 1
    assert links[0].display_active is False

