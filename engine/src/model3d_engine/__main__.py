"""Engine entry point: python -m model3d_engine."""

import logging
import subprocess
import sys
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from model3d_engine.api.app import create_app
from model3d_engine.config import Settings, is_loopback_host, load_settings
from model3d_contracts.jobs import JobError

from model3d_engine.gpu.modes import ModeName, ModePlan, ModeUnavailable, choose_mode, placement
from model3d_engine.gpu.probe import Inventory, probe_gpus
from model3d_engine.gpu.topology import normalize_bus_id
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner

SHAPE_FACTORY = "model3d_engine.stages.hunyuan:make_shape_stage"
PAINT_FACTORY = "model3d_engine.stages.hunyuan:make_paint_stage"
logger = logging.getLogger(__name__)
MEMORY_COMMAND = ["nvidia-smi", "--query-gpu=pci.bus_id,memory.used,memory.total", "--format=csv,noheader,nounits"]


def read_memory() -> dict[str, tuple[int, int]]:
    """Used and total MiB per GPU bus id, from nvidia-smi."""
    result = subprocess.run(MEMORY_COMMAND, check=True, capture_output=True, text=True, timeout=30)
    memory = {}
    for line in result.stdout.splitlines():
        if line.strip():
            bus, used, total = (part.strip() for part in line.split(","))
            memory[normalize_bus_id(bus)] = (int(used), int(total))
    return memory


def build_engine(
    settings: Settings,
    inventory: Inventory | None = None,
    shape_factory: str = SHAPE_FACTORY,
    paint_factory: str = PAINT_FACTORY,
    memory_reader: Callable[[], dict[str, tuple[int, int]]] = read_memory,
) -> FastAPI:
    """Pick the run mode for the detected GPUs and wire workers, scheduler, store, and bus into the API."""
    inventory = probe_gpus() if inventory is None else inventory
    forced = ModeName(settings.mode) if settings.mode is not None else None
    try:
        plan = choose_mode(inventory, forced, settings.shape_bus, settings.paint_bus)
    except ModeUnavailable as error:
        plan = ModePlan(ModeName.unsupported, False, error.message, None, None)
    logger.info("Run mode %s: %s", plan.name.value, plan.reason)
    devices = placement(plan)
    env = stage_env(settings)
    hunyuan_dir = str(settings.hunyuan_dir)
    shape = ProcessStageRunner(
        shape_factory,
        {"hunyuan_dir": hunyuan_dir, "device": devices.shape},
        env,
        name="shape",
        task_timeout_s=settings.stage_timeout_s,
    )
    paint = ProcessStageRunner(
        paint_factory,
        {"hunyuan_dir": hunyuan_dir, "device": devices.paint, "helper_device": devices.helpers},
        env,
        name="paint",
        task_timeout_s=settings.stage_timeout_s,
    )
    store, bus = JobStore(settings.jobs_dir), EventBus()
    swap = plan.name in (ModeName.single_gpu_swap, ModeName.shape_only)
    scheduler = Scheduler(store, bus, shape, paint, swap=swap)
    if plan.name == ModeName.unsupported:
        scheduler.start_error = JobError(code=ModeUnavailable.code, message=plan.reason)

    def gpu_entry(gpu, memory: dict[str, tuple[int, int]]) -> dict:
        uses_shape, uses_paint = gpu == plan.shape, gpu == plan.paint
        stage = "shape+paint" if uses_shape and uses_paint else "shape" if uses_shape else "paint" if uses_paint else None
        ready = (uses_shape and shape.ready) or (uses_paint and paint.ready)
        used, total = memory.get(gpu.bus_id, (None, None))
        return {
            "bus_id": gpu.bus_id,
            "stage": stage,
            "ready": ready,
            "memory_used_mib": used,
            "memory_total_mib": total,
            "memory_free_mib": gpu.memory_free_mib,
        }

    def health() -> dict:
        try:
            memory = memory_reader()
        except Exception as error:
            logger.warning("Could not read GPU memory: %s", error)
            memory = {}
        return {
            "ready": scheduler.ready,
            "mode": {"name": plan.name.value, "texture": plan.texture, "reason": plan.reason},
            "gpus": [gpu_entry(gpu, memory) for gpu in inventory.gpus],
        }

    # Only shape_only refuses textured jobs; with no usable GPU every job gets 503 and the cause.
    texture_available = plan.name != ModeName.shape_only
    return create_app(settings, scheduler, store, bus, health, texture_available=texture_available)


def main() -> None:
    """Serve the engine on the configured loopback host and port."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if not is_loopback_host(settings.host):
        print("MODEL3D_HOST must be a loopback address", file=sys.stderr)
        raise SystemExit(2)
    uvicorn.run(build_engine(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
