"""Engine entry point: python -m model3d_engine."""

import dataclasses
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from model3d_engine.api.app import create_app
from model3d_engine.config import REPO_ROOT, Settings, is_loopback_host, load_settings
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
from model3d_engine.net import BindError, bind_sockets, listen_hosts, write_engine_info
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.head_pass import HeadPassRunner
from model3d_engine.stages.hunyuan import stage_env
from model3d_engine.stages.process import ProcessStageRunner
from model3d_engine.stages.protocol import NoStage, StageRunner
from model3d_engine.stages.rig import RigRunner

SHAPE_FACTORY = "model3d_engine.stages.hunyuan:make_shape_stage"
PAINT_FACTORY = "model3d_engine.stages.hunyuan:make_paint_stage"
TRELLIS_FACTORY = "model3d_engine.stages.trellis:make_trellis_stage"
VOICE_FACTORY = "model3d_engine.stages.voice:make_voice_stage"
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


TEXTURING_ENCODER = "ckpts/shape_enc_next_dc_f16c32_fp16.safetensors"


def texturing_weights_reason(hf_home: Path) -> str | None:
    """Why TRELLIS.2's texturing pipeline cannot load, or None: it needs a shape encoder the
    image-to-3D pipeline does not use, so a cache made for generation alone lacks it."""
    snapshots = Path(hf_home) / "hub/models--microsoft--TRELLIS.2-4B/snapshots"
    if any((snapshot / TEXTURING_ENCODER).exists() for snapshot in snapshots.glob("*")):
        return None
    return f"TRELLIS.2's texturing weights are missing ({TEXTURING_ENCODER} in {snapshots})"


def build_engine(
    settings: Settings,
    inventory: Inventory | None = None,
    shape_factory: str = SHAPE_FACTORY,
    paint_factory: str = PAINT_FACTORY,
    generator_factory: str = TRELLIS_FACTORY,
    memory_reader: Callable[[], dict[str, tuple[int, int]]] = read_memory,
    rig_runner: RigRunner | None = None,
    head_pass: HeadPassRunner | None = None,
    voice_runner: StageRunner | None = None,
    texture_pass: HeadPassRunner | None = None,
    voice_factory: str = VOICE_FACTORY,
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
    if head_pass is None and trellis and rig is not None and (rig.ready or getattr(rig, "reason", None) is None):
        candidate = HeadPassRunner(
            Path(sys.executable),
            REPO_ROOT / "scripts/kit_assembly/run.py",
            settings.home,
            f"http://127.0.0.1:{settings.port}",
        )
        head_pass = candidate if candidate.reason is None else None
    if texture_pass is None and trellis and rig is not None and (rig.ready or getattr(rig, "reason", None) is None):
        # Sharper texture retextures each skeleton part with TRELLIS.2 on the cards itself.
        candidate = HeadPassRunner(
            Path(sys.executable),
            REPO_ROOT / "scripts/kit_assembly/texture_pass.py",
            settings.home,
            f"http://127.0.0.1:{settings.port}",
        )
        texture_pass = candidate if candidate.reason is None else None
    # Speech runs on the shape card, taking turns with the 3D stages; it needs the Qwen3-TTS clone and its venv.
    voice = voice_runner
    voice_gpu = devices.shape if plan.name != ModeName.unsupported else None
    if voice is None and voice_gpu is not None and (settings.qwen_tts_dir / ".venv/bin/python").is_file():
        voice = ProcessStageRunner(
            voice_factory,
            {"qwen_dir": str(settings.qwen_tts_dir), "device": voice_gpu, "hf_home": str(settings.hf_home)},
            env,
            name="voice",
            task_timeout_s=settings.stage_timeout_s,
        )
    cards["voice"] = devices.shape
    store, bus = JobStore(settings.jobs_dir), EventBus()
    swap = plan.name in (ModeName.single_gpu_swap, ModeName.single_gpu_resident, ModeName.shape_only)
    scheduler = Scheduler(store, bus, shape, paint, swap=swap, rig=rig, cards=cards, head_pass=head_pass, voice=voice,
                          texture_pass=texture_pass)
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
            "detailed_face": detailed_face_reason() is None,
            "detailed_face_reason": detailed_face_reason(),
            "voice": scheduler.voice_available,
            "voice_reason": voice_reason(),
            "sharper_texture": sharper_texture_reason() is None,
            "sharper_texture_reason": sharper_texture_reason(),
        }

    def rig_reason() -> str | None:
        if rig is None:
            return "Rigging needs a usable GPU"
        return None if rig.ready else getattr(rig, "reason", None)

    def voice_reason() -> str | None:
        if voice is not None:
            return None
        if voice_gpu is None:
            return "Voice generation needs a usable GPU"
        return f"Qwen3-TTS is not installed in {settings.qwen_tts_dir}"

    def sharper_texture_reason() -> str | None:
        if not trellis:
            return "Sharper texture needs MODEL3D_GENERATOR=trellis2"
        if (reason := rig_reason()) is not None:
            return reason
        if texture_pass is None:
            return "The texture-pass tools are not installed"
        return texturing_weights_reason(settings.hf_home)

    def detailed_face_reason() -> str | None:
        if not trellis:
            return "Detailed face needs MODEL3D_GENERATOR=trellis2"
        if (reason := rig_reason()) is not None:
            return reason
        if head_pass is None:
            return "The head-pass tools are not installed"
        return head_pass.reason

    # Only shape_only refuses textured jobs; with no usable GPU every job gets 503 and the cause.
    texture_available = plan.name != ModeName.shape_only
    app = create_app(settings, scheduler, store, bus, health, texture_available=texture_available, rig_reason=rig_reason, detailed_face_reason=detailed_face_reason, sharper_texture_reason=sharper_texture_reason, voice_reason=voice_reason)
    app.state.rig = rig
    app.state.voice = voice
    app.state.head_pass = head_pass
    return app


def main() -> None:
    """Serve the engine on loopback and, when configured, the LAN address, on one port."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if not is_loopback_host(settings.host):
        print("MODEL3D_HOST must be a loopback address", file=sys.stderr)
        raise SystemExit(2)
    if settings.lan_host and not settings.api_keys:
        print("MODEL3D_LAN_HOST needs MODEL3D_API_KEYS: clients on the network must send a key", file=sys.stderr)
        raise SystemExit(2)
    hosts = listen_hosts(settings)
    try:
        sockets = bind_sockets(hosts, settings.port)
    except BindError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from error
    port = sockets[0].getsockname()[1]
    write_engine_info(settings.home, port, hosts)
    for host in hosts:
        logger.info("Listening on %s", f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}")
    uvicorn.Server(uvicorn.Config(build_engine(dataclasses.replace(settings, port=port)))).run(sockets=sockets)


if __name__ == "__main__":
    main()
