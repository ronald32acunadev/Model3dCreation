"""Measure the VRAM each pipeline stage holds, to size the engine's run modes.

Run from the repository root with the engine environment, with LM Studio and Ollama
models unloaded:

    uv run --project engine python scripts/measure_stage_vram.py [--bus 0000:29:00.0] [--image PATH] [--qualities final,draft] [--no-resident]

It prints each GPU's idle free VRAM, then, for each requested quality preset, the VRAM the
shape and paint workers hold after loading and at their peak while running the input image
on one card, and finally whether both workers fit on that card at once. Memory is read from
nvidia-smi, so it includes each worker's CUDA context.
"""

import argparse
import asyncio
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from model3d_contracts.jobs import JobOptions, Quality
from model3d_engine.config import load_settings
from model3d_engine.gpu.topology import normalize_bus_id
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner
from model3d_engine.stages.protocol import StageError, StageTask

REPO_ROOT = Path(__file__).resolve().parents[1]
PENGUIN = REPO_ROOT / "engine/tests/assets/penguin.png"
SHAPE_FACTORY = "model3d_engine.stages.hunyuan:make_shape_stage"
PAINT_FACTORY = "model3d_engine.stages.hunyuan:make_paint_stage"
MEMORY_COMMAND = ["nvidia-smi", "--query-gpu=pci.bus_id,memory.used,memory.free", "--format=csv,noheader,nounits"]
MIN_FREE_MIB = 10 * 1024


def read_memory() -> dict[str, tuple[int, int]]:
    """Used and free MiB per bus id."""
    out = subprocess.run(MEMORY_COMMAND, check=True, capture_output=True, text=True, timeout=30).stdout
    memory = {}
    for line in out.splitlines():
        if line.strip():
            bus, used, free = (part.strip() for part in line.split(","))
            memory[normalize_bus_id(bus)] = (int(used), int(free))
    return memory


class PeakSampler:
    """Samples one GPU's used MiB every 0.2 s in a thread and keeps the peak."""

    def __init__(self, bus: str):
        self.bus = bus
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, read_memory()[self.bus][0])
            self._stop.wait(0.2)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def make_runner(stage: str, device: str, env: dict[str, str], hunyuan_dir: str) -> ProcessStageRunner:
    if stage == "shape":
        return ProcessStageRunner(SHAPE_FACTORY, {"hunyuan_dir": hunyuan_dir, "device": device}, env, name="shape")
    kwargs = {"hunyuan_dir": hunyuan_dir, "device": device, "helper_device": "cpu"}
    return ProcessStageRunner(PAINT_FACTORY, kwargs, env, name="paint")


def new_job(root: Path, quality: Quality, image: Path) -> StageTask:
    job_dir = Path(tempfile.mkdtemp(dir=root))
    (job_dir / f"input{image.suffix.lower()}").write_bytes(image.read_bytes())
    return StageTask(job_id=job_dir.name, job_dir=job_dir, options=JobOptions(quality=quality), seed=1234)


async def measure_stage(stage: str, task: StageTask, bus: str, device: str, env, hunyuan_dir) -> dict:
    """Load one stage alone on the card, run the task, and return its memory and time."""
    idle = read_memory()[bus][0]
    runner = make_runner(stage, device, env, hunyuan_dir)
    with PeakSampler(bus) as sampler:
        started = time.monotonic()
        await runner.start()
        loaded_at = time.monotonic()
        loaded = read_memory()[bus][0]
        try:
            await runner.run(task, lambda value, text: None)
        finally:
            finished = time.monotonic()
            await runner.stop()
    return {
        "loaded_mib": loaded - idle,
        "peak_mib": sampler.peak - idle,
        "load_s": loaded_at - started,
        "run_s": finished - loaded_at,
    }


async def measure_resident(task: StageTask, bus: str, device: str, env, hunyuan_dir) -> str:
    """Load both stages on the card at once and run one textured job through them."""
    idle = read_memory()[bus][0]
    shape = make_runner("shape", device, env, hunyuan_dir)
    paint = make_runner("paint", device, env, hunyuan_dir)
    with PeakSampler(bus) as sampler:
        try:
            await shape.start()
            await paint.start()
            await shape.run(task, lambda value, text: None)
            await paint.run(task, lambda value, text: None)
            outcome = "fits"
        except StageError as error:
            outcome = f"does not fit ({error.code}: {error.message[:120]})"
        finally:
            await shape.stop()
            await paint.stop()
    return f"{outcome}; peak {sampler.peak - idle} MiB above idle"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bus", default="0000:29:00.0", help="PCI bus id of the card to measure on")
    parser.add_argument("--image", type=Path, default=PENGUIN, help="input image for every job")
    parser.add_argument("--qualities", default="final,draft", help="comma-separated quality presets to measure, in order")
    parser.add_argument("--no-resident", action="store_true", help="skip the both-workers-resident check")
    args = parser.parse_args()
    bus = normalize_bus_id(args.bus)

    memory = read_memory()
    print("| GPU | Idle used MiB | Idle free MiB |\n| :--- | ---: | ---: |")
    for gpu_bus, (used, free) in sorted(memory.items()):
        print(f"| {gpu_bus} | {used} | {free} |")
    if bus not in memory:
        print(f"GPU {bus} not found")
        return 1
    if memory[bus][1] < MIN_FREE_MIB:
        print(f"{bus} has {memory[bus][1]} MiB free; unload LM Studio/Ollama models first")
        return 1
    if not args.image.is_file():
        print(f"Image not found: {args.image}")
        return 1
    try:
        qualities = [Quality(name.strip()) for name in args.qualities.split(",") if name.strip()]
    except ValueError:
        print(f"Unknown quality in --qualities: {args.qualities}")
        return 1

    settings = load_settings()
    env = stage_env(settings)
    hunyuan_dir = str(settings.hunyuan_dir)
    device = f"cuda:{sorted(memory).index(bus)}"  # CUDA_DEVICE_ORDER=PCI_BUS_ID in env
    root = Path(tempfile.mkdtemp(prefix="model3d-vram-"))

    print("\n| Stage | Quality | Held after load MiB | Peak MiB | Load s | Run s | Job folder |\n| :--- | :--- | ---: | ---: | ---: | ---: | :--- |")
    for quality in qualities:
        task = new_job(root, quality, args.image)
        for stage in ("shape", "paint"):
            row = await measure_stage(stage, task, bus, device, env, hunyuan_dir)
            print(
                f"| {stage} | {quality.value} | {row['loaded_mib']} | {row['peak_mib']} "
                f"| {row['load_s']:.1f} | {row['run_s']:.1f} | {task.job_dir.name} |",
                flush=True,
            )

    if not args.no_resident:
        print("\nBoth workers resident on", bus, "(final):", await measure_resident(new_job(root, Quality.final, args.image), bus, device, env, hunyuan_dir))
    print("Job folders:", root)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
