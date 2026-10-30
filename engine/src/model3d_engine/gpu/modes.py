"""Run mode chosen from the GPU inventory: where and when the shape and paint stages run."""

from dataclasses import dataclass, replace
from enum import Enum

from model3d_engine.gpu.probe import GpuInfo, Inventory
from model3d_engine.gpu.topology import Placement

# nvidia-smi MiB above idle, measured with scripts/measure_stage_vram.py (docs/architecture.md,
# section 2). They include each worker's CUDA context. "Need" is the peak while running a
# `final` job, except shape, whose peak is the `high` job's (octree 512; 8957 at 384, measured
# 2026-10-29); "held" is what a loaded, idle worker keeps.
SHAPE_NEED_MIB = 10111
SHAPE_HELD_MIB = 7216
PAINT_NEED_MIB = 10168
PAINT_HELD_MIB = 4938
# The owner chose 512 MiB: with 1 GiB, paint no longer fits the card that drives the desktop.
MARGIN_MIB = 512
# TRELLIS.2 1024_cascade peak with low_vram on the marine test image, measured 2026-10-29
# (docs/userstories/alternative-shape-models/).
TRELLIS_NEED_MIB = 10690
# Split peaks of the 2026-10-30 marine measurement (docs/userstories/alternative-shape-models/):
# the image encoder and flow models run on the generating card.
TRELLIS_GEN_NEED_MIB = 5180
# The decoders and CuMesh baking run on the decoding card (2026-10-30 marine measurement).
TRELLIS_POST_NEED_MIB = 10120


class ModeName(str, Enum):
    dual_gpu = "dual_gpu"
    single_gpu_resident = "single_gpu_resident"
    single_gpu_swap = "single_gpu_swap"
    shape_only = "shape_only"
    unsupported = "unsupported"


@dataclass(frozen=True)
class ModePlan:
    """The chosen mode; `shape` and `paint` are the same GPU in the single-GPU modes."""

    name: ModeName
    texture: bool
    reason: str
    shape: GpuInfo | None
    paint: GpuInfo | None


class ModeUnavailable(Exception):
    """MODEL3D_MODE or the bus overrides ask for something this machine cannot do."""

    code = "mode_unavailable"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def choose_generator_mode(inventory: Inventory) -> ModePlan:
    """TRELLIS.2 splits generation and decoding across two cards when both fit, else runs on one card."""
    usable = sorted((gpu for gpu in inventory.gpus if gpu.supported), key=lambda gpu: gpu.memory_free_mib, reverse=True)
    if not usable:
        return ModePlan(ModeName.unsupported, False, inventory.problem or "No supported NVIDIA GPU was found", None, None)
    post = next((gpu for gpu in usable if _fits(gpu, TRELLIS_POST_NEED_MIB)), None)
    if post is not None:
        gen = next((gpu for gpu in usable if gpu != post and _fits(gpu, TRELLIS_GEN_NEED_MIB)), None)
        if gen is not None:
            reason = f"TRELLIS.2 generates on {gen.bus_id} and decodes on {post.bus_id}"
            return ModePlan(ModeName.dual_gpu, True, reason, gen, post)
    best = usable[0]
    if _fits(best, TRELLIS_NEED_MIB):
        reason = f"TRELLIS.2 generates shape and texture on {best.bus_id}"
        return ModePlan(ModeName.single_gpu_resident, True, reason, best, best)
    reason = (
        f"No GPU has enough free VRAM for TRELLIS.2: {best.bus_id} has {_gib(best.memory_free_mib)} GiB free "
        f"and it needs {_gib(TRELLIS_NEED_MIB + MARGIN_MIB)} GiB"
    )
    return ModePlan(ModeName.unsupported, False, reason, None, None)


def placement(plan: ModePlan) -> Placement:
    """CUDA devices for the plan's stages; paint's helpers always run on the CPU."""
    shape = f"cuda:{plan.shape.index}" if plan.shape is not None else "cpu"
    paint = f"cuda:{plan.paint.index}" if plan.paint is not None else shape
    return Placement(shape=shape, paint=paint, helpers="cpu")


def _gib(mib: int) -> str:
    return f"{mib / 1024:.1f}"


def _fits(gpu: GpuInfo, need_mib: int) -> bool:
    return gpu.memory_free_mib >= need_mib + MARGIN_MIB


def _fits_both(gpu: GpuInfo) -> bool:
    # Each worker peaks while the other one sits loaded and idle.
    both = max(SHAPE_HELD_MIB + PAINT_NEED_MIB, PAINT_HELD_MIB + SHAPE_NEED_MIB)
    return gpu.memory_free_mib >= both + MARGIN_MIB


def _dual(shape: GpuInfo, paint: GpuInfo) -> ModePlan:
    reason = f"Two GPUs: shape on {shape.bus_id}, paint on {paint.bus_id}"
    return ModePlan(ModeName.dual_gpu, True, reason, shape, paint)


def _single(name: ModeName, gpu: GpuInfo) -> ModePlan:
    free = _gib(gpu.memory_free_mib)
    if name == ModeName.single_gpu_resident:
        return ModePlan(name, True, f"One GPU with {free} GiB free: shape and paint stay loaded", gpu, gpu)
    if name == ModeName.single_gpu_swap:
        return ModePlan(name, True, f"One GPU with {free} GiB free: shape and paint take turns", gpu, gpu)
    reason = f"One GPU with {free} GiB free: enough for shape but not for paint, so models have no texture"
    return ModePlan(ModeName.shape_only, False, reason, gpu, None)


def _by_free_memory(gpus: list[GpuInfo]) -> list[GpuInfo]:
    """Most free VRAM first; the faster PCIe link breaks ties."""
    return sorted(gpus, key=lambda gpu: (gpu.memory_free_mib, gpu.bandwidth_gbps), reverse=True)


def _automatic(usable: list[GpuInfo], problem: str | None) -> ModePlan:
    if not usable:
        return ModePlan(ModeName.unsupported, False, problem or "No supported NVIDIA GPU was found", None, None)
    # Paint, the larger stage, gets the card with the most free VRAM (the owner's rule: the
    # most headroom against out-of-memory); shape gets the next one.
    for paint in _by_free_memory([gpu for gpu in usable if _fits(gpu, PAINT_NEED_MIB)]):
        shapes = _by_free_memory([gpu for gpu in usable if gpu != paint and _fits(gpu, SHAPE_NEED_MIB)])
        if shapes:
            return _dual(shapes[0], paint)
    best = max(usable, key=lambda gpu: gpu.memory_free_mib)
    if _fits_both(best):
        return _single(ModeName.single_gpu_resident, best)
    if _fits(best, PAINT_NEED_MIB) and _fits(best, SHAPE_NEED_MIB):
        return _single(ModeName.single_gpu_swap, best)
    if _fits(best, SHAPE_NEED_MIB):
        return _single(ModeName.shape_only, best)
    reason = (
        f"No GPU has enough free VRAM: {best.bus_id} has {_gib(best.memory_free_mib)} GiB free "
        f"and shape needs {_gib(SHAPE_NEED_MIB + MARGIN_MIB)} GiB"
    )
    return ModePlan(ModeName.unsupported, False, reason, None, None)


def _forced(plan: ModePlan) -> ModePlan:
    return replace(plan, reason=f"{plan.reason} (forced by MODEL3D_MODE)")


def choose_mode(
    inventory: Inventory,
    forced: ModeName | None = None,
    shape_bus: str | None = None,
    paint_bus: str | None = None,
) -> ModePlan:
    """Pick the best mode for the inventory, or honor MODEL3D_MODE and the bus overrides.

    A forced mode or explicit buses skip the VRAM margin: the person takes the
    out-of-memory risk. What the hardware cannot do at all raises ModeUnavailable.
    """
    usable = [gpu for gpu in inventory.gpus if gpu.supported]
    if shape_bus is not None and paint_bus is not None:
        by_bus = {gpu.bus_id: gpu for gpu in usable}
        for bus in (shape_bus, paint_bus):
            if bus not in by_bus:
                raise ModeUnavailable(f"GPU {bus} is not visible to PyTorch")
        shape, paint = by_bus[shape_bus], by_bus[paint_bus]
        if shape != paint:
            if forced not in (None, ModeName.dual_gpu):
                raise ModeUnavailable(f"MODEL3D_MODE={forced.value} needs MODEL3D_SHAPE_BUS and MODEL3D_PAINT_BUS to match")
            return _dual(shape, paint)
        if forced == ModeName.dual_gpu:
            raise ModeUnavailable("MODEL3D_MODE=dual_gpu needs MODEL3D_SHAPE_BUS and MODEL3D_PAINT_BUS to differ")
        return _single(forced or ModeName.single_gpu_swap, shape)
    if forced is None:
        return _automatic(usable, inventory.problem)
    if not usable:
        raise ModeUnavailable(inventory.problem or "No supported NVIDIA GPU was found")
    if forced == ModeName.dual_gpu:
        if len(usable) < 2:
            raise ModeUnavailable(f"MODEL3D_MODE=dual_gpu needs two GPUs; found {len(usable)}")
        paint, shape = _by_free_memory(usable)[:2]
        return _forced(_dual(shape, paint))
    return _forced(_single(forced, max(usable, key=lambda gpu: gpu.memory_free_mib)))
