"""The install recipe: every step from Python 3.11 to the weights, as data for the runner."""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from model3d_client.setup.actions import apply_patch, copy_tree, download, extract_tarball, patch_file
from model3d_client.setup.checks import Toolchain
from model3d_client.setup.runner import Command, Step, StepContext, StepFailed

HUNYUAN_COMMIT = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
UNIRIG_COMMIT = "6793c6640ff01c8fb389f3993434124bb43d2933"
QWEN_COMMIT = "022e286b98fbec7e1e916cb940cdf532cd9f488e"
HUNYUAN_TARBALL = f"https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/archive/{HUNYUAN_COMMIT}.tar.gz"
UNIRIG_TARBALL = f"https://github.com/VAST-AI-Research/UniRig/archive/{UNIRIG_COMMIT}.tar.gz"
QWEN_TARBALL = f"https://github.com/QwenLM/Qwen3-TTS/archive/{QWEN_COMMIT}.tar.gz"
REALESRGAN_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
PYTORCH_INDEX = "https://download.pytorch.org/whl/cu130"
PYG_WHEELS = "https://data.pyg.org/whl/torch-2.9.1+cu130.html"
PATCH = Path("engine/patches/hunyuan3d-paint-device.patch")
SITE = ".venv/lib/python3.11/site-packages"
COMPONENT_SIZES = {"base": 31 * 10**9, "skeletons": 8 * 10**9, "voice": 9 * 10**9}  # measured on the reference install

# Run by the engine's Python: upstream's build script picks the system python3-config and the wrong ABI suffix.
INPAINT_SCRIPT = """\
import subprocess, sysconfig, pybind11
subprocess.run(["c++", "-O3", "-Wall", "-shared", "-std=c++11", "-fPIC",
                "-I" + sysconfig.get_paths()["include"], "-I" + pybind11.get_include(),
                "mesh_inpaint_processor.cpp", "-o",
                "mesh_inpaint_processor" + sysconfig.get_config_var("EXT_SUFFIX")], check=True)
"""
WEIGHTS_SCRIPT = """\
from huggingface_hub import snapshot_download as get
get("tencent/Hunyuan3D-2.1", allow_patterns=["hunyuan3d-dit-v2-1/*", "hunyuan3d-paintpbr-v2-1/*"])
get("facebook/dinov2-giant", allow_patterns=["*.json", "model.safetensors"])
import rembg
rembg.new_session("birefnet-general")
"""
UNIRIG_CHECKPOINTS = ("skeleton/articulation-xl_quantization_256/model.ckpt", "skin/articulation-xl/model.ckpt")
# The skeleton model also reads facebook/opt-350m's config (AutoConfig) while the engine keeps the hub offline.
UNIRIG_WEIGHTS_SCRIPT = f"""\
from huggingface_hub import hf_hub_download as get
for name in {UNIRIG_CHECKPOINTS!r}:
    get("VAST-AI/UniRig", name)
get("facebook/opt-350m", "config.json")
"""
VOICE_WEIGHTS_SCRIPT = """\
from huggingface_hub import snapshot_download as get
get("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
get("Qwen/Qwen3-TTS-12Hz-1.7B-Base")
get("openai/whisper-large-v3-turbo")
"""
# Exits 1 when the engine would start in mode unsupported (no GPU with the free VRAM for any mode).
MODE_SCRIPT = """\
from model3d_engine.gpu.modes import ModeName, choose_mode
from model3d_engine.gpu.probe import probe_gpus
plan = choose_mode(probe_gpus())
print("engine mode:", plan.name.value, "-", plan.reason)
raise SystemExit(plan.name == ModeName.unsupported)
"""


@dataclass(frozen=True)
class Sources:
    """Where the app's own files are: /opt/model3d when installed, the repository in development."""

    app_root: Path
    uv: Path
    app_version: str


def _fingerprint(*parts: str | bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode() if isinstance(part, str) else part)
        digest.update(b"\0")
    return digest.hexdigest()


def _any(root: Path, pattern: str) -> bool:
    return next(root.glob(pattern), None) is not None


def build_recipe(sources: Sources, install_dir: Path, toolchain: Toolchain, components: list[str]) -> list[Step]:
    """The steps for the chosen components, in install order."""
    f = install_dir
    uv = str(sources.uv)
    engine, contracts = f / "engine", f / "contracts"
    hunyuan, unirig, qwen = f / "vendor/Hunyuan3D-2.1", f / "vendor/UniRig", f / "vendor/Qwen3-TTS"
    hf, downloads = f / "data/hf", f / "data/downloads"
    engine_python, unirig_python, qwen_python = str(engine / ".venv/bin/python"), str(unirig / ".venv/bin/python"), str(qwen / ".venv/bin/python")
    patch = engine / "patches" / PATCH.name
    lock = (sources.app_root / "engine/uv.lock").read_bytes()
    patch_bytes = (sources.app_root / PATCH).read_bytes() if (sources.app_root / PATCH).is_file() else b""
    uv_env = {"UV_PYTHON_INSTALL_DIR": str(f / "python"), "UV_CACHE_DIR": str(f / "data/uv-cache"),
              "UV_PYTHON_PREFERENCE": "only-managed"}
    build_env = {"CUDA_HOME": str(toolchain.cuda_home), "TORCH_CUDA_ARCH_LIST": toolchain.arch_list,
                 "MAX_JOBS": str(toolchain.max_jobs)}
    uv_build_env = {**uv_env, **build_env}

    def copy_sources(ctx: StepContext) -> None:
        if sources.app_root.resolve() == f.resolve():
            ctx.log("Sources are already in the install folder")
            return
        copy_tree(sources.app_root / "engine", engine, ctx)
        copy_tree(sources.app_root / "contracts", contracts, ctx)
        (f / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources.app_root / "scripts/verify_gpus.py", f / "scripts/verify_gpus.py")

    def fetch_hunyuan(ctx: StepContext) -> None:
        archive = downloads / f"hunyuan-{HUNYUAN_COMMIT}.tar.gz"
        download(HUNYUAN_TARBALL, archive, ctx)
        extract_tarball(archive, hunyuan, ctx)
        download(REALESRGAN_URL, hunyuan / "hy3dpaint/ckpt/RealESRGAN_x4plus.pth", ctx)
        apply_patch(patch, hunyuan, ctx)

    def patch_snapshot(ctx: StepContext) -> None:
        # diffusers loads the snapshot's copy of the attention processor, not the clone's.
        for path in hf.glob("hub/models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-paintpbr-v2-1/unet/attn_processor.py"):
            patch_file(patch, path, ctx)

    def verify(ctx: StepContext) -> None:
        ctx.run(Command([engine_python, str(f / "scripts/verify_gpus.py")], cwd=f, env=build_env))
        # verify_gpus only warns about busy GPUs; the install must not pass on a check it could not run.
        try:
            ctx.run(Command([engine_python, "-c", MODE_SCRIPT], cwd=f, env=build_env))
        except StepFailed as error:
            raise StepFailed("gpu_busy", "No GPU has enough free VRAM for the engine right now", "") from error
        (f / "data/logs").mkdir(parents=True, exist_ok=True)
        (f / "data/logs/verify.ok").write_text("ok")

    def fetch_unirig(ctx: StepContext) -> None:
        archive = downloads / f"unirig-{UNIRIG_COMMIT}.tar.gz"
        download(UNIRIG_TARBALL, archive, ctx)
        extract_tarball(archive, unirig, ctx)

    requirements = f / "data/uv-cache/unirig-requirements.txt"

    def write_requirements(ctx: StepContext) -> None:
        # flash-attn is built last, without build isolation, for this GPU's architecture.
        lines = (unirig / "requirements.txt").read_text().splitlines()
        requirements.parent.mkdir(parents=True, exist_ok=True)
        requirements.write_text("\n".join(line for line in lines if "flash_attn" not in line) + "\n")

    def unirig_weights_done(folder: Path) -> bool:
        snapshots = folder / "data/hf/hub/models--VAST-AI--UniRig/snapshots"
        checkpoints = any(all((snapshot / name).is_file() for name in UNIRIG_CHECKPOINTS)
                          for snapshot in snapshots.glob("*"))
        return checkpoints and _any(folder / "data/hf", "hub/models--facebook--opt-350m/snapshots/*/config.json")

    def fetch_qwen(ctx: StepContext) -> None:
        archive = downloads / f"qwen3-tts-{QWEN_COMMIT}.tar.gz"
        download(QWEN_TARBALL, archive, ctx)
        extract_tarball(archive, qwen, ctx)

    def voice_weights_done(folder: Path) -> bool:
        return (_any(folder / "data/hf", "hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-VoiceDesign/snapshots/*")
                and _any(folder / "data/hf", "hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-Base/snapshots/*")
                and _any(folder / "data/hf", "hub/models--openai--whisper-large-v3-turbo/snapshots/*"))

    def uv_pip(*args: str, env: dict[str, str] | None = None) -> Command:
        return Command([uv, "pip", "install", "--python", ".venv", *args], cwd=unirig, env=env or uv_build_env)

    steps = [
        Step("python", "Python 3.11", "base", 0, _fingerprint("python", "3.11"),
             [Command([uv, "python", "install", "3.11"], env=uv_env)],
             lambda folder: _any(folder / "python", "cpython-3.11*")),
        Step("source", "App sources", "base", 0, _fingerprint("source", sources.app_version, lock),
             [copy_sources],
             lambda folder: (folder / "engine/pyproject.toml").is_file() and (folder / "contracts/pyproject.toml").is_file()),
        Step("hunyuan", "Hunyuan3D 2.1", "base", 0, _fingerprint("hunyuan", HUNYUAN_COMMIT, patch_bytes),
             [fetch_hunyuan],
             lambda folder: (folder / "vendor/Hunyuan3D-2.1/hy3dshape").is_dir()
             and (folder / "vendor/Hunyuan3D-2.1/hy3dpaint/ckpt/RealESRGAN_x4plus.pth").is_file()),
        # The first sync installs PyTorch, which basicsr and custom_rasterizer import at build time.
        Step("engine-env", "Engine environment", "base", 0, _fingerprint("engine-env", lock, toolchain.arch_list),
             [Command([uv, "sync", "--frozen", "--no-dev", "--no-install-package", "basicsr",
                       "--no-install-package", "realesrgan", "--no-install-package", "custom-rasterizer"],
                      cwd=engine, env=uv_build_env),
              Command([uv, "sync", "--frozen", "--no-dev"], cwd=engine, env=uv_build_env)],
             lambda folder: _any(folder / "engine" / SITE, "custom_rasterizer*")),
        Step("inpaint", "Mesh inpainting module", "base", 0, _fingerprint("inpaint", HUNYUAN_COMMIT, lock),
             [Command([engine_python, "-c", INPAINT_SCRIPT], cwd=hunyuan / "hy3dpaint/DifferentiableRenderer",
                      env=build_env)],
             lambda folder: _any(folder / "vendor/Hunyuan3D-2.1/hy3dpaint/DifferentiableRenderer",
                                 "mesh_inpaint_processor*.so")),
        Step("weights", "Model weights", "base", COMPONENT_SIZES["base"],
             _fingerprint("weights", HUNYUAN_COMMIT, patch_bytes, "birefnet-general"),
             [Command([engine_python, "-c", WEIGHTS_SCRIPT], env={"HF_HOME": str(hf), "U2NET_HOME": str(f / "data/u2net")}),
              patch_snapshot],
             lambda folder: _any(folder / "data/hf", "hub/models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-dit-v2-1")
             and _any(folder / "data/hf", "hub/models--facebook--dinov2-giant/snapshots/*/model.safetensors")
             and (folder / "data/u2net/birefnet-general.onnx").is_file()),
        Step("verify", "Check the GPUs", "base", 0,
             _fingerprint("verify", sources.app_version, lock, toolchain.arch_list),
             [verify], lambda folder: (folder / "data/logs/verify.ok").is_file()),
    ]
    if "skeletons" in components:
        steps += [
            Step("unirig-source", "UniRig", "skeletons", 0, _fingerprint("unirig-source", UNIRIG_COMMIT),
                 [fetch_unirig], lambda folder: (folder / "vendor/UniRig/run.py").is_file()),
            Step("unirig-env", "UniRig environment", "skeletons", 0,
                 _fingerprint("unirig-env", UNIRIG_COMMIT, toolchain.arch_list),
                 [Command([uv, "venv", "--python", "3.11", ".venv"], cwd=unirig, env=uv_build_env),
                  uv_pip("torch==2.9.1", "torchvision==0.24.1", "--index-url", PYTORCH_INDEX),
                  write_requirements,
                  uv_pip("-r", str(requirements), "spconv-cu126", "numpy==1.26.4", "ninja", "packaging", "psutil",
                         "setuptools", "wheel"),
                  uv_pip("torch_scatter", "torch_cluster", "-f", PYG_WHEELS),
                  uv_pip("--no-build-isolation", "flash-attn",
                         env={**uv_build_env, "FLASH_ATTN_CUDA_ARCHS": toolchain.arch_list.replace(".", ""),
                              "NVCC_THREADS": "2"})],
                 lambda folder: _any(folder / "vendor/UniRig" / SITE, "flash_attn*")),
            Step("unirig-weights", "UniRig weights", "skeletons", COMPONENT_SIZES["skeletons"],
                 _fingerprint("unirig-weights", UNIRIG_COMMIT),
                 [Command([unirig_python, "-c", UNIRIG_WEIGHTS_SCRIPT], env={"HF_HOME": str(hf)})],
                 unirig_weights_done),
        ]
    if "voice" in components:
        steps += [
            Step("voice-source", "Qwen3-TTS", "voice", 0, _fingerprint("voice-source", QWEN_COMMIT),
                 [fetch_qwen], lambda folder: (folder / "vendor/Qwen3-TTS/README.md").is_file() or (folder / "vendor/Qwen3-TTS/setup.py").is_file()),
            Step("voice-env", "Voice environment", "voice", 0,
                 _fingerprint("voice-env", QWEN_COMMIT, toolchain.arch_list),
                 [Command([uv, "venv", "--python", "3.11", ".venv"], cwd=qwen, env=uv_build_env),
                  Command([uv, "pip", "install", "--python", ".venv", "torch==2.9.1", "torchvision==0.24.1", "torchaudio==2.9.1",
                           "--index-url", PYTORCH_INDEX], cwd=qwen, env=uv_build_env),
                  Command([uv, "pip", "install", "--python", ".venv", "qwen-tts", "soundfile", "librosa",
                           "transformers", "huggingface_hub"], cwd=qwen, env=uv_build_env)],
                 lambda folder: (folder / "vendor/Qwen3-TTS/.venv/bin/python").is_file()
                 and _any(folder / "vendor/Qwen3-TTS" / SITE, "qwen_tts*")),
            Step("voice-weights", "Voice weights", "voice", COMPONENT_SIZES["voice"],
                 _fingerprint("voice-weights", QWEN_COMMIT),
                 [Command([qwen_python, "-c", VOICE_WEIGHTS_SCRIPT], env={"HF_HOME": str(hf)})],
                 voice_weights_done),
        ]
    return steps
