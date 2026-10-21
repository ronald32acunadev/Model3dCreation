from model3d_engine.gpu.probe import arch_supported, merge_inventory, probe_gpus
from model3d_engine.gpu.topology import parse_nvidia_smi

LINKS = parse_nvidia_smi(
    "00000000:25:00.0, NVIDIA GeForce RTX 5070, 2, 2, 12227\n"
    "00000000:29:00.0, NVIDIA GeForce RTX 5070, 3, 16, 12227\n"
)


def raw(index, bus, free_mib=11000, major=12, minor=0, supported=True):
    return {
        "index": index,
        "bus_id": bus,
        "name": "NVIDIA GeForce RTX 5070",
        "total_mib": 12227,
        "free_mib": free_mib,
        "major": major,
        "minor": minor,
        "supported": supported,
    }


def test_probe_merge_two_cards():
    inventory = merge_inventory([raw(0, "0000:25:00.0"), raw(1, "0000:29:00.0", free_mib=10500)], LINKS)
    assert inventory.problem is None
    shape, paint = inventory.gpus
    assert (shape.index, shape.bus_id, shape.bandwidth_gbps) == (0, "0000:25:00.0", 1.0)
    assert (paint.index, paint.bus_id, paint.bandwidth_gbps) == (1, "0000:29:00.0", 15.76)
    assert (paint.memory_total_mib, paint.memory_free_mib, paint.capability, paint.supported) == (12227, 10500, (12, 0), True)


def test_probe_merge_ignores_cards_torch_cannot_see():
    # CUDA_VISIBLE_DEVICES hides GPU0: nvidia-smi still lists it, torch does not.
    inventory = merge_inventory([raw(0, "0000:29:00.0")], LINKS)
    assert [(gpu.index, gpu.bus_id) for gpu in inventory.gpus] == [(0, "0000:29:00.0")]


def test_probe_merge_without_nvidia_smi():
    inventory = merge_inventory([raw(0, "0000:29:00.0")], None)
    assert inventory.gpus[0].bandwidth_gbps == 0.0
    assert inventory.problem is None


def test_probe_merge_no_gpus():
    inventory = merge_inventory([], LINKS)
    assert inventory.gpus == ()
    assert inventory.problem == "No NVIDIA GPU is visible to PyTorch"


def test_probe_merge_unsupported_capability():
    old = raw(0, "0000:01:00.0", major=6, minor=1, supported=False)
    inventory = merge_inventory([old], None)
    assert inventory.gpus[0].supported is False
    assert inventory.problem == "GPU 0000:01:00.0 (sm_61) is not supported by this PyTorch build"
    assert merge_inventory([old, raw(1, "0000:29:00.0")], LINKS).problem is None


def test_arch_supported():
    arch_list = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120", "compute_120"]
    assert arch_supported(12, 0, arch_list) is True
    assert arch_supported(6, 1, arch_list) is False
    assert arch_supported(13, 0, arch_list) is True  # runs through the compute_120 PTX
    assert arch_supported(13, 0, ["sm_120"]) is False


def test_probe_failure_reports_reason():
    failed = probe_gpus(timeout_s=30, target="tests.probe_stubs:raising_probe")
    assert failed.gpus == ()
    assert failed.problem.startswith("GPU probe failed:")
    reported = probe_gpus(timeout_s=30, target="tests.probe_stubs:reporting_probe")
    assert reported.problem == "GPU probe failed: CUDA driver version is insufficient"
    hung = probe_gpus(timeout_s=1, target="tests.probe_stubs:hanging_probe")
    assert hung.problem == "GPU probe did not answer within 1 s"
