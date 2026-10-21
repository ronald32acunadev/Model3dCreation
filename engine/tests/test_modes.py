import pytest

from model3d_engine.gpu.modes import (
    MARGIN_MIB,
    PAINT_HELD_MIB,
    PAINT_NEED_MIB,
    SHAPE_HELD_MIB,
    SHAPE_NEED_MIB,
    ModeName,
    ModeUnavailable,
    choose_mode,
)
from model3d_engine.gpu.probe import GpuInfo, Inventory

# Measured peaks and held memory include each worker's CUDA context (docs/architecture.md).
BOTH_STAGES_MIB = max(SHAPE_HELD_MIB + PAINT_NEED_MIB, PAINT_HELD_MIB + SHAPE_NEED_MIB) + MARGIN_MIB


def gpu(bus, free_mib, total_mib=12227, bandwidth=15.76, supported=True, index=0):
    return GpuInfo(index, bus, "GPU", total_mib, free_mib, (12, 0) if supported else (6, 1), supported, bandwidth)


def inventory(*gpus):
    return Inventory(tuple(gpus), None if gpus else "No NVIDIA GPU is visible to PyTorch")


# Idle free VRAM measured on the owner's machine (Task 1): GPU1 also drives the desktop.
OWNER = inventory(gpu("0000:25:00.0", 11761, bandwidth=1.0, index=0), gpu("0000:29:00.0", 11166, index=1))


def test_owner_machine_is_dual_gpu():
    plan = choose_mode(OWNER)
    assert plan.name == ModeName.dual_gpu
    assert (plan.shape.bus_id, plan.paint.bus_id) == ("0000:25:00.0", "0000:29:00.0")
    assert plan.texture is True
    assert plan.reason == "Two GPUs: shape on 0000:25:00.0, paint on 0000:29:00.0"


def test_thresholds_match_the_measurement():
    assert (SHAPE_NEED_MIB, SHAPE_HELD_MIB, PAINT_NEED_MIB, PAINT_HELD_MIB, MARGIN_MIB) == (8957, 7216, 10168, 4938, 512)


def test_one_big_card_is_resident():
    assert choose_mode(inventory(gpu("0000:01:00.0", BOTH_STAGES_MIB - 1, total_mib=24564))).name == ModeName.single_gpu_swap
    plan = choose_mode(inventory(gpu("0000:01:00.0", BOTH_STAGES_MIB, total_mib=24564)))
    assert plan.name == ModeName.single_gpu_resident
    assert plan.shape == plan.paint
    assert plan.texture is True


def test_one_card_for_one_stage_swaps():
    plan = choose_mode(inventory(gpu("0000:01:00.0", 11264)))
    assert plan.name == ModeName.single_gpu_swap
    assert plan.shape.bus_id == plan.paint.bus_id == "0000:01:00.0"
    assert plan.reason == "One GPU with 11.0 GiB free: shape and paint take turns"


def test_card_that_fits_shape_only():
    if PAINT_NEED_MIB <= SHAPE_NEED_MIB:
        pytest.skip("paint needs no more VRAM than shape, so shape_only is only reachable when forced")
    plan = choose_mode(inventory(gpu("0000:01:00.0", SHAPE_NEED_MIB + MARGIN_MIB)))
    assert plan.name == ModeName.shape_only
    assert plan.texture is False
    assert plan.paint is None


def test_too_small_and_no_gpu_are_unsupported():
    small = choose_mode(inventory(gpu("0000:01:00.0", 4096, total_mib=6144)))
    assert small.name == ModeName.unsupported
    assert small.texture is False
    assert "4.0 GiB free" in small.reason
    none = choose_mode(inventory())
    assert none.name == ModeName.unsupported
    assert none.reason == "No NVIDIA GPU is visible to PyTorch"


def test_unsupported_card_is_skipped():
    plan = choose_mode(inventory(gpu("0000:01:00.0", 11264), gpu("0000:02:00.0", 30000, supported=False, index=1)))
    assert plan.name == ModeName.single_gpu_swap
    assert plan.shape.bus_id == "0000:01:00.0"


def test_desktop_card_too_busy_falls_back_to_swap():
    plan = choose_mode(inventory(gpu("0000:25:00.0", 11761, bandwidth=1.0), gpu("0000:29:00.0", 6144, index=1)))
    assert plan.name == ModeName.single_gpu_swap
    assert plan.shape.bus_id == "0000:25:00.0"


def test_mixed_cards_use_most_free():
    plan = choose_mode(inventory(gpu("0000:01:00.0", 5120, total_mib=6144), gpu("0000:02:00.0", 11264, index=1)))
    assert plan.name == ModeName.single_gpu_swap
    assert plan.shape.bus_id == "0000:02:00.0"


def test_three_cards_use_two_best():
    plan = choose_mode(
        inventory(
            gpu("0000:01:00.0", 11264, bandwidth=3.94, index=0),
            gpu("0000:02:00.0", 11264, bandwidth=15.76, index=1),
            gpu("0000:03:00.0", 11264, bandwidth=7.88, index=2),
        )
    )
    assert plan.name == ModeName.dual_gpu
    assert (plan.paint.bus_id, plan.shape.bus_id) == ("0000:02:00.0", "0000:03:00.0")


def test_forced_modes():
    one = inventory(gpu("0000:01:00.0", 11264))
    with pytest.raises(ModeUnavailable) as raised:
        choose_mode(one, forced=ModeName.dual_gpu)
    assert raised.value.code == "mode_unavailable"
    assert raised.value.message == "MODEL3D_MODE=dual_gpu needs two GPUs; found 1"
    resident = choose_mode(one, forced=ModeName.single_gpu_resident)  # the margin check is skipped
    assert resident.name == ModeName.single_gpu_resident
    assert resident.reason.endswith("(forced by MODEL3D_MODE)")
    shape_only = choose_mode(OWNER, forced=ModeName.shape_only)
    assert shape_only.texture is False and shape_only.paint is None
    with pytest.raises(ModeUnavailable) as raised:
        choose_mode(inventory(), forced=ModeName.single_gpu_swap)
    assert raised.value.message == "No NVIDIA GPU is visible to PyTorch"


def test_bus_overrides():
    same = choose_mode(OWNER, shape_bus="0000:29:00.0", paint_bus="0000:29:00.0")
    assert same.name == ModeName.single_gpu_swap
    assert same.shape.bus_id == "0000:29:00.0"
    resident = choose_mode(
        OWNER, forced=ModeName.single_gpu_resident, shape_bus="0000:29:00.0", paint_bus="0000:29:00.0"
    )
    assert resident.name == ModeName.single_gpu_resident
    swapped = choose_mode(OWNER, shape_bus="0000:29:00.0", paint_bus="0000:25:00.0")
    assert swapped.name == ModeName.dual_gpu
    assert (swapped.shape.bus_id, swapped.paint.bus_id) == ("0000:29:00.0", "0000:25:00.0")
    with pytest.raises(ModeUnavailable) as raised:
        choose_mode(OWNER, shape_bus="0000:25:00.0", paint_bus="0000:41:00.0")
    assert raised.value.message == "GPU 0000:41:00.0 is not visible to PyTorch"
