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

from model3d_engine.gpu.modes import (
    ModeName,
    ModePlan,
    ModeUnavailable,
    choose_generator_mode,
    choose_mode,
    placement,
)
from model3d_engine.gpu.probe import Inventory, probe_gpus
from model3d_engine.gpu.topology import normalize_bus_id
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner
from model3d_engine.stages.protocol import NoStage
from model3d_engine.stages.rig import RigRunner

SHAPE_FACTORY = "model3d_engine.stages.hunyuan:make_shape_stage"
PAINT_FACTORY = "model3d_engine.stages.hunyuan:make_paint_stage"
TRELLIS_FACTORY = "model3d_engine.stages.trellis:make_trellis_stage"
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
    generator_factory: str = TRELLIS_FACTORY,
    memory_reader: Callable[[], dict[str, tuple[int, int]]] = read_memory,
    rig_runner: RigRunner | None = None,
) -> FastAPI:
    """Pick the run mode for the detected GPUs and wire workers, scheduler, store, and bus into the API."""
    inventory = probe_gpus() if inventory is None else inventory
    trellis = settings.generator == "trellis2"
    if trellis:
        plan = choose_generator_mode(inventory)
    else:
        forced = ModeName(settings.mode) if settings.mode is not None else None
        try:
            plan = choose_mode(inventory, forced, settings.shape_bus, settings.paint_bus)
        except ModeUnavailable as error:
            plan = ModePlan(ModeName.unsupported, False, error.message, None, None)
    logger.info("Run mode %s: %s", plan.name.value, plan.reason)
    devices = placement(plan)
    env = stage_env(settings)
    if trellis:
        shape = ProcessStageRunner(
            generator_factory,
            {
                "trellis_dir": str(settings.trellis_dir),
                "device": devices.shape,
                "hf_home": str(settings.hf_home),
                "post_device": devices.paint if plan.name == ModeName.dual_gpu else None,
            },
            env,
            name="generate",
            task_timeout_s=settings.stage_timeout_s,
        )
        paint = NoStage()
    else:
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
    # Rigging runs on the paint card (the shape card in shape_only), taking turns with that
    # stage; with no usable GPU there is nothing to rig on.
    rig = rig_runner
    rig_gpu = (plan.paint if plan.texture else plan.shape) if plan.name != ModeName.unsupported else None
    if rig is None and rig_gpu is not None:
        rig = RigRunner(
            settings.unirig_dir,
            settings.hf_home,
            devices.paint if plan.texture else devices.shape,
            env=env,
            task_timeout_s=settings.stage_timeout_s,
        )
    if trellis:
        # One TRELLIS.2 job takes turns on one lock, even when its decode runs on the other card.
        cards = {"shape": devices.shape, "paint": devices.shape, "rig": devices.shape}
    else:
        cards = {"shape": devices.shape, "paint": devices.paint, "rig": devices.paint if plan.texture else devices.shape}
    store, bus = JobStore(settings.jobs_dir), EventBus()
    swap = plan.name in (ModeName.single_gpu_swap, ModeName.single_gpu_resident, ModeName.shape_only)
    scheduler = Scheduler(store, bus, shape, paint, swap=swap, rig=rig, cards=cards)
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
            "rig": scheduler.rig_available,
            "rig_reason": rig_reason(),
        }

    def rig_reason() -> str | None:
        if rig is None:
            return "Rigging needs a usable GPU"
        return None if rig.ready else getattr(rig, "reason", None)

    # Only shape_only refuses textured jobs; with no usable GPU every job gets 503 and the cause.
    texture_available = plan.name != ModeName.shape_only
    app = create_app(settings, scheduler, store, bus, health, texture_available=texture_available, rig_reason=rig_reason)
    app.state.rig = rig
    return app


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
