"""Engine entry point: python -m model3d_engine."""

import ipaddress
import subprocess
import sys
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from model3d_engine.api.app import create_app
from model3d_engine.config import Settings, load_settings
from model3d_engine.gpu.topology import (
    NVIDIA_SMI_COMMAND,
    GpuLink,
    normalize_bus_id,
    parse_nvidia_smi,
    resolve_placement,
)
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner

SHAPE_FACTORY = "model3d_engine.stages.hunyuan:make_shape_stage"
PAINT_FACTORY = "model3d_engine.stages.hunyuan:make_paint_stage"
MEMORY_COMMAND = ["nvidia-smi", "--query-gpu=pci.bus_id,memory.used,memory.total", "--format=csv,noheader,nounits"]


def read_links() -> list[GpuLink]:
    """PCIe links of the installed GPUs, from nvidia-smi."""
    result = subprocess.run(NVIDIA_SMI_COMMAND, check=True, capture_output=True, text=True, timeout=30)
    return parse_nvidia_smi(result.stdout)


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
    links: list[GpuLink] | None = None,
    shape_factory: str = SHAPE_FACTORY,
    paint_factory: str = PAINT_FACTORY,
    memory_reader: Callable[[], dict[str, tuple[int, int]]] = read_memory,
) -> FastAPI:
    """Wire the stage workers, scheduler, store, and event bus into the API."""
    placement = resolve_placement(read_links() if links is None else links, settings.shape_bus, settings.paint_bus)
    env = stage_env(settings)
    hunyuan_dir = str(settings.hunyuan_dir)
    shape = ProcessStageRunner(shape_factory, {"hunyuan_dir": hunyuan_dir, "device": placement.shape}, env, name="shape")
    paint = ProcessStageRunner(
        paint_factory,
        {"hunyuan_dir": hunyuan_dir, "device": placement.paint, "helper_device": placement.helpers},
        env,
        name="paint",
    )
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, shape, paint)

    def health() -> dict:
        memory = memory_reader()
        gpus = []
        for bus_id, stage, runner in ((settings.shape_bus, "shape", shape), (settings.paint_bus, "paint", paint)):
            used, total = memory.get(bus_id, (None, None))
            gpus.append(
                {"bus_id": bus_id, "stage": stage, "ready": runner.ready, "memory_used_mib": used, "memory_total_mib": total}
            )
        return {"ready": scheduler.ready, "gpus": gpus}

    return create_app(settings, scheduler, store, bus, health)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    """Serve the engine on the configured loopback host and port."""
    settings = load_settings()
    if not _is_loopback(settings.host):
        print("MODEL3D_HOST must be a loopback address", file=sys.stderr)
        raise SystemExit(2)
    uvicorn.run(build_engine(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
