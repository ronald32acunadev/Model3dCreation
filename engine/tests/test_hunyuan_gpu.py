"""End-to-end generation on both GPUs. Needs the full environment from docs/setup.md
and free VRAM (unload LM Studio/Ollama models first)."""

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
import trimesh

from model3d_contracts.jobs import JobOptions, JobState
from model3d_engine.config import load_settings
from model3d_engine.gpu.topology import NVIDIA_SMI_COMMAND, parse_nvidia_smi, resolve_placement
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner

PENGUIN = Path(__file__).parent / "assets" / "penguin.png"

pytestmark = [pytest.mark.gpu, pytest.mark.anyio]


async def test_penguin_end_to_end(tmp_path):
    settings = load_settings()
    links = parse_nvidia_smi(subprocess.run(NVIDIA_SMI_COMMAND, check=True, capture_output=True, text=True).stdout)
    placement = resolve_placement(links, settings.shape_bus or "0000:25:00.0", settings.paint_bus or "0000:29:00.0")
    env = stage_env(settings)
    hunyuan_dir = str(settings.hunyuan_dir)
    shape = ProcessStageRunner(
        "model3d_engine.stages.hunyuan:make_shape_stage",
        {"hunyuan_dir": hunyuan_dir, "device": placement.shape},
        env,
        name="shape",
    )
    paint = ProcessStageRunner(
        "model3d_engine.stages.hunyuan:make_paint_stage",
        {"hunyuan_dir": hunyuan_dir, "device": placement.paint, "helper_device": placement.helpers},
        env,
        name="paint",
    )
    store, bus = JobStore(tmp_path), EventBus()
    scheduler = Scheduler(store, bus, shape, paint)
    started = time.monotonic()
    await scheduler.start()
    loaded = time.monotonic()
    try:
        record = store.create(JobOptions(), PENGUIN.read_bytes(), ".png")
        events = bus.subscribe(record)
        await events.__anext__()
        scheduler.submit(record)
        stage_started = {}
        async for event in events:
            if event.type == "progress" and event.stage not in stage_started:
                stage_started[event.stage] = time.monotonic()
            if event.type in ("completed", "failed", "cancelled"):
                final = event
        finished = time.monotonic()
    finally:
        await scheduler.stop()

    print(
        f"\nload {loaded - started:.1f}s, shape {stage_started['paint'] - stage_started['shape']:.1f}s, "
        f"paint {finished - stage_started['paint']:.1f}s"
    )
    assert record.state == JobState.succeeded, record.error
    assert final.stats.textured is True and final.stats.faces > 1000
    scene = trimesh.load(record.dir / "model.glb")
    materials = [geometry.visual.material for geometry in scene.geometry.values()]
    assert any(getattr(material, "baseColorTexture", None) is not None for material in materials)
