import importlib.util
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_gpus.py"
spec = importlib.util.spec_from_file_location("verify_gpus", SCRIPT)
verify_gpus = importlib.util.module_from_spec(spec)
sys.modules["verify_gpus"] = verify_gpus
spec.loader.exec_module(verify_gpus)

# Real output on the reference workstation.
NVIDIA_SMI_OUTPUT = """00000000:25:00.0, NVIDIA GeForce RTX 5070, 2, 2, 12227
00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227
"""
NVCC_OUTPUT = """Cuda compilation tools, release 13.1, V13.1.115
Build cuda_13.1.r13.1/compiler.37061995_0
"""
PLACEMENT = {
    "shape": verify_gpus.GpuLink("0000:25:00.0", "RTX 5070", 2, 2, 12227),
    "paint": verify_gpus.GpuLink("0000:29:00.0", "RTX 5070", 3, 16, 12227),
}


def test_normalize_bus_id():
    assert verify_gpus.normalize_bus_id("00000000:25:00.0") == "0000:25:00.0"
    assert verify_gpus.normalize_bus_id("0000:29:00.0") == "0000:29:00.0"
    assert verify_gpus.normalize_bus_id(" 00000000:2A:00.0 ") == "0000:2a:00.0"
    with pytest.raises(ValueError):
        verify_gpus.normalize_bus_id("GPU-1234")
    with pytest.raises(ValueError):
        verify_gpus.normalize_bus_id("")


def test_bus_id_from_props():
    assert verify_gpus.bus_id_from_props(0, 37, 0) == "0000:25:00.0"


def test_parse_nvidia_smi():
    first, second = verify_gpus.parse_nvidia_smi(NVIDIA_SMI_OUTPUT)
    assert (first.bus_id, first.pcie_gen, first.pcie_width, first.memory_mib) == ("0000:25:00.0", 2, 2, 12227)
    assert (second.bus_id, second.pcie_gen, second.pcie_width, second.memory_mib) == ("0000:29:00.0", 3, 16, 12227)
    assert first.bandwidth_gbps == pytest.approx(1.0)
    assert second.bandwidth_gbps == pytest.approx(15.76)


def test_parse_nvidia_smi_skips_blank_lines():
    assert len(verify_gpus.parse_nvidia_smi(NVIDIA_SMI_OUTPUT + "\n\n")) == 2


def test_parse_nvidia_smi_rejects_bad_lines():
    with pytest.raises(ValueError):
        verify_gpus.parse_nvidia_smi("00000000:25:00.0, RTX 5070, 2, 2")
    with pytest.raises(ValueError):
        verify_gpus.parse_nvidia_smi("00000000:25:00.0, RTX 5070, 2, [N/A], 12227")


def test_plan_placement_puts_paint_on_the_widest_link():
    links = verify_gpus.parse_nvidia_smi(NVIDIA_SMI_OUTPUT)
    for ordering in (links, list(reversed(links))):
        placement = verify_gpus.plan_placement(ordering)
        assert placement["shape"].bus_id == "0000:25:00.0"
        assert placement["paint"].bus_id == "0000:29:00.0"


def test_plan_placement_tie_goes_to_lower_bus_id():
    links = [
        verify_gpus.GpuLink("0000:29:00.0", "RTX 5070", 3, 16, 12227),
        verify_gpus.GpuLink("0000:25:00.0", "RTX 5070", 3, 16, 12227),
    ]
    assert verify_gpus.plan_placement(links)["paint"].bus_id == "0000:25:00.0"


def test_plan_placement_requires_two_gpus():
    links = verify_gpus.parse_nvidia_smi(NVIDIA_SMI_OUTPUT)
    with pytest.raises(ValueError, match="expected 2 GPUs, found 1"):
        verify_gpus.plan_placement(links[:1])
    with pytest.raises(ValueError, match="expected 2 GPUs, found 3"):
        verify_gpus.plan_placement(links + links[:1])


def test_parse_nvcc_version():
    assert verify_gpus.parse_nvcc_version(NVCC_OUTPUT) == (13, 1)
    with pytest.raises(ValueError):
        verify_gpus.parse_nvcc_version("garbage")


def test_cuda_majors_match():
    assert verify_gpus.cuda_majors_match("13.0", (13, 1)) is True
    assert verify_gpus.cuda_majors_match("12.8", (13, 1)) is False
    assert verify_gpus.cuda_majors_match(None, (13, 1)) is False


def test_format_report():
    checks = [verify_gpus.Check("torch", "pass", "2.9.1"), verify_gpus.Check("cuda available", "pass", "")]
    report = verify_gpus.format_report(checks, PLACEMENT)
    assert report.endswith("RESULT: OK")
    assert "shape -> 0000:25:00.0" in report
    assert "paint -> 0000:29:00.0" in report

    warn_only = verify_gpus.format_report([verify_gpus.Check("free VRAM cuda:0", "warn", "1.0 GiB free")], None)
    assert warn_only.endswith("RESULT: OK")

    report_fail = verify_gpus.format_report([verify_gpus.Check("nvcc", "fail", "missing")], None)
    assert report_fail.endswith("RESULT: FAILED (1 checks)")
    assert "[FAIL] nvcc: missing" in report_fail


def test_main_exit_codes(monkeypatch, capsys):
    passing = [verify_gpus.Check("torch", "pass", "2.9.1"), verify_gpus.Check("free VRAM cuda:0", "warn", "1.0 GiB free")]
    monkeypatch.setattr(verify_gpus, "run_checks", lambda repo_root: (passing, PLACEMENT))
    assert verify_gpus.main() == 0
    assert capsys.readouterr().out.rstrip().endswith("RESULT: OK")

    failing = passing + [verify_gpus.Check("nvcc", "fail", "missing")]
    monkeypatch.setattr(verify_gpus, "run_checks", lambda repo_root: (failing, None))
    assert verify_gpus.main() == 1
    assert capsys.readouterr().out.rstrip().endswith("RESULT: FAILED (1 checks)")


@pytest.mark.gpu
def test_real_environment_passes():
    checks, placement = verify_gpus.run_checks(REPO_ROOT)
    report = verify_gpus.format_report(checks, placement)
    assert not [check for check in checks if check.status == "fail"], report
