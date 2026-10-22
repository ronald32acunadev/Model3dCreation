"""End-to-end generation on real GPUs. Needs the full environment from docs/setup.md
and free VRAM (unload LM Studio/Ollama models first)."""

import subprocess
import time
from pathlib import Path

import pytest
import trimesh

from model3d_contracts.jobs import JobOptions, JobState, Quality
from model3d_engine.config import load_settings
from model3d_engine.gpu.modes import ModeName, ModePlan, choose_mode, placement
from model3d_engine.gpu.probe import probe_gpus
from model3d_engine.gpu.topology import normalize_bus_id
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner

PENGUIN = Path(__file__).parent / "assets" / "penguin.png"

pytestmark = [pytest.mark.gpu, pytest.mark.anyio]


def build_scheduler(plan: ModePlan, tmp_path: Path) -> tuple[Scheduler, JobStore, EventBus]:
    settings = load_settings()
    devices = placement(plan)
    env = stage_env(settings)
    hunyuan_dir = str(settings.hunyuan_dir)
    shape = ProcessStageRunner(
        "model3d_engine.stages.hunyuan:make_shape_stage",
        {"hunyuan_dir": hunyuan_dir, "device": devices.shape},
        env,
        name="shape",
    )
    paint = ProcessStageRunner(
        "model3d_engine.stages.hunyuan:make_paint_stage",
        {"hunyuan_dir": hunyuan_dir, "device": devices.paint, "helper_device": devices.helpers},
        env,
        name="paint",
    )
    store, bus = JobStore(tmp_path), EventBus()
    return Scheduler(store, bus, shape, paint, swap=plan.name == ModeName.single_gpu_swap), store, bus


async def run_job(scheduler: Scheduler, store: JobStore, bus: EventBus, options: JobOptions):
    """Submit the penguin and wait for its final event; return the record, the event, and
    the shape and paint seconds."""
    record = store.create(options, PENGUIN.read_bytes(), ".png")
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
    assert record.state == JobState.succeeded, record.error
    assert final.stats.textured is True and final.stats.faces > 1000
    scene = trimesh.load(record.dir / "model.glb")
    materials = [geometry.visual.material for geometry in scene.geometry.values()]
    assert any(getattr(material, "baseColorTexture", None) is not None for material in materials)
    return record, final, stage_started["paint"] - stage_started["shape"], finished - stage_started["paint"]


async def run_penguin(plan: ModePlan, tmp_path: Path) -> tuple[float, float, float]:
    """Run the penguin with texture in the plan's mode; return load, shape, and paint seconds
    (in swap mode, paint includes the swap)."""
    scheduler, store, bus = build_scheduler(plan, tmp_path)
    started = time.monotonic()
    await scheduler.start()
    loaded = time.monotonic()
    try:
        _record, _final, shape_s, paint_s = await run_job(scheduler, store, bus, JobOptions())
    finally:
        await scheduler.stop()
    timings = (loaded - started, shape_s, paint_s)
    print(f"\n{plan.name.value} ({plan.reason}): load {timings[0]:.1f}s, shape {timings[1]:.1f}s, paint {timings[2]:.1f}s")
    return timings


async def test_penguin_end_to_end(tmp_path):
    """The default mode on the reference machine; MODEL3D_SHAPE_BUS/MODEL3D_PAINT_BUS force a placement."""
    settings = load_settings()
    plan = choose_mode(probe_gpus(), shape_bus=settings.shape_bus, paint_bus=settings.paint_bus)
    assert plan.name == ModeName.dual_gpu, plan.reason
    await run_penguin(plan, tmp_path)


async def test_penguin_swap_mode_on_one_card(tmp_path, monkeypatch):
    """Hide every card but the one with the most free VRAM, as on a one-GPU machine."""
    rows = subprocess.run(
        ["nvidia-smi", "--query-gpu=pci.bus_id,uuid,memory.free", "--format=csv,noheader,nounits"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    cards = [tuple(part.strip() for part in row.split(",")) for row in rows if row.strip()]
    bus, uuid, _free = max(cards, key=lambda card: int(card[2]))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", uuid)  # the probe and the workers inherit it
    inventory = probe_gpus()
    assert [gpu.bus_id for gpu in inventory.gpus] == [normalize_bus_id(bus)]
    plan = choose_mode(inventory)
    assert plan.name == ModeName.single_gpu_swap, plan.reason
    await run_penguin(plan, tmp_path)


async def test_high_detail_then_final_on_one_paint_worker(tmp_path):
    """High detail paints at 768 px with the memory savers; the next final job must switch the
    same worker back (docs/spikes/paint-high-detail.md)."""
    settings = load_settings()
    plan = choose_mode(probe_gpus(), shape_bus=settings.shape_bus, paint_bus=settings.paint_bus)
    scheduler, store, bus = build_scheduler(plan, tmp_path)
    await scheduler.start()
    try:
        _high, high_event, _shape_s, high_paint_s = await run_job(
            scheduler, store, bus, JobOptions(quality=Quality.high, seed=7)
        )
        _final, final_event, _shape_s, final_paint_s = await run_job(
            scheduler, store, bus, JobOptions(quality=Quality.final, seed=7)
        )
    finally:
        await scheduler.stop()
    print(f"\nhigh: paint {high_paint_s:.1f}s, {high_event.stats.faces} faces; "
          f"final: paint {final_paint_s:.1f}s, {final_event.stats.faces} faces")
    assert high_event.stats.faces > 100_000
    assert final_event.stats.faces <= 40_000
