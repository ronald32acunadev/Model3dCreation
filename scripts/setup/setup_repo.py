"""Installs what Model3D needs inside this repository: the engine, its models, UniRig, and TRELLIS.2.

The wrapper scripts in this folder prepare uv and client/.venv, then run this file with the
client's Python. The pins come from the client's install recipe, so this script and the
wizard cannot disagree.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from model3d_client.setup.actions import apply_patch, download, extract_tarball, patch_file
from model3d_client.setup.recipe import (
    HUNYUAN_COMMIT,
    HUNYUAN_TARBALL,
    INPAINT_SCRIPT,
    PATCH,
    PYG_WHEELS,
    PYTORCH_INDEX,
    REALESRGAN_URL,
    UNIRIG_CHECKPOINTS,
    UNIRIG_COMMIT,
    UNIRIG_TARBALL,
    UNIRIG_WEIGHTS_SCRIPT,
    WEIGHTS_SCRIPT,
)
from model3d_client.setup.runner import Command, StepContext
from model3d_client.setup.state import read_pointer

CUDA_DIR = Path(os.environ.get("MODEL3D_SETUP_CUDA_DIR") or (Path.home() / ".local/share/model3d/cuda-13.2"))
CUDA_VERSION = "13.2"
CUDA_PACKAGES = (
    "nvidia-cuda-nvcc==13.2.86",
    "nvidia-cuda-cccl==13.2.86",
    "nvidia-cuda-crt==13.2.86",
    "nvidia-cuda-runtime==13.2.86",
    "nvidia-nvvm==13.2.86",
    # torch's headers include these libraries' headers, so extensions cannot compile without them.
    "nvidia-cublas==13.0.0.19",
    "nvidia-cufft==12.0.0.15",
    "nvidia-curand==10.4.0.35",
    "nvidia-cusolver==12.0.3.29",
    "nvidia-cusparse==12.6.2.49",
    "nvidia-nvjitlink==13.0.39",
    "nvidia-nvtx==13.0.39",
)
MIN_FREE_BYTES = 100 * 10**9
RETRY_ATTEMPTS = 5
RETRY_PAUSE_S = 20.0
ENGINE_VENV = Path("engine/.venv")
TRELLIS_DIR = Path("vendor/TRELLIS.2")
TRELLIS_COMMIT = "75fbf0183001ed9876c8dbb35de6b68552ee08bd"
TRELLIS_URL = "https://github.com/microsoft/TRELLIS.2.git"
NVDIFFRAST_COMMIT = "253ac4fcea7de5f396371124af597e6cc957bfae"
CUMESH_COMMIT = "12289e1062f0603f2f0d0771b02e1395d247f26f"
FLEXGEMM_COMMIT = "6dd94a859c26ee8246888502eada3dd8ad85532e"
EXTENSIONS = (
    ("nvdiffrast", "https://github.com/NVlabs/nvdiffrast.git", NVDIFFRAST_COMMIT),
    ("CuMesh", "https://github.com/JeffreyXiang/CuMesh.git", CUMESH_COMMIT),
    ("FlexGEMM", "https://github.com/JeffreyXiang/FlexGEMM.git", FLEXGEMM_COMMIT),
)
TRELLIS_TORCH = ("torch==2.9.0", "torchvision==0.24.0", "xformers==0.0.33.post1")
TRELLIS_PACKAGES = (
    "imageio", "imageio-ffmpeg", "tqdm", "easydict", "opencv-python-headless", "ninja", "trimesh",
    "transformers>=4.56,<5", "pandas", "zstandard", "kornia", "timm", "pillow", "setuptools", "wheel",
    "safetensors", "huggingface_hub",
    "git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8",
)
TRELLIS_WEIGHTS_SCRIPT = """\
from huggingface_hub import snapshot_download as get
get("microsoft/TRELLIS.2-4B", allow_patterns=[
    "*.json", "ckpts/ss_flow_img_dit_1_3B_64_bf16.*", "ckpts/slat_flow_img2shape_dit_1_3B_*",
    "ckpts/slat_flow_imgshape2tex_dit_1_3B_*", "ckpts/shape_dec_next_dc_f16c32_fp16.*",
    "ckpts/tex_dec_next_dc_f16c32_fp16.*"])
get("microsoft/TRELLIS-image-large", allow_patterns=["ckpts/ss_dec_conv3d_16l8_fp16.*", "pipeline.json"])
"""
DINOV3_REPO = "facebook/dinov3-vitl16-pretrain-lvd1689m"
DINOV3_SCRIPT = """\
from huggingface_hub import snapshot_download as get
get("facebook/dinov3-vitl16-pretrain-lvd1689m", allow_patterns=["*.json", "model.safetensors"])
"""
DINOV3_SKIPPED = (
    "DINOv3 needs Hugging Face access: request it at https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m, "
    "run `engine/.venv/bin/huggingface-cli login`, then run this script again."
)
NO_DRIVER = (
    "NVIDIA driver not found: install the driver from https://www.nvidia.com/drivers and reboot, "
    "then run this script again"
)
ENGINE_BLOCKED = (
    "The engine cannot install on Windows yet: engine/pyproject.toml allows Linux only. "
    "Porting it is tracked in docs/userstories/cross-platform-native/README.md."
)
MACOS_DONE = (
    "Client installed. The engine needs an NVIDIA GPU with CUDA, so generating models is not available on a Mac."
)
TOKEN_RELATIVE = Path(".cache/huggingface/token")
LOG_DIR = Path("data/setup-logs")
PLATFORMS = {"linux": "linux", "win32": "windows", "darwin": "macos"}
SYSTEM_MARKERS = {"linux": "sys_platform == 'linux'", "windows": "sys_platform == 'win32'",
                  "macos": "sys_platform == 'darwin'"}


class Skipped(Exception):
    """A step that cannot finish yet for a reason the user can fix; the run continues."""


class Blocked(Exception):
    """A step the system cannot perform; the run stops."""


class CommandFailed(Exception):
    """A command that exited with an error."""


@dataclass(frozen=True)
class Probes:
    run: Callable[[list[str]], str]
    disk_free: Callable[[Path], int]
    has_hf_access: Callable[[str], bool]


@dataclass(frozen=True)
class Options:
    rig: bool = True
    trellis: bool = True
    home: Path = field(default_factory=Path.home)
    cpu_count: int = field(default_factory=lambda: os.cpu_count() or 1)


Runner = Callable[[Command, Callable[[str], None]], None]


def _never() -> bool:
    return False


@dataclass(frozen=True)
class Context:
    repo: Path
    log: Callable[[str], None]
    runner: Runner

    def run(self, command: Command) -> None:
        self.runner(command, self.log)

    def step_context(self) -> StepContext:
        return StepContext(install_dir=self.repo, log=self.log, cancelled=_never, run=self.run)


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    done: Callable[[], bool]
    run: Callable[[Context], None]


def _exists(root: Path, pattern: str) -> bool:
    return any(path.exists() for path in root.glob(pattern))


def _link(link: Path, target: str) -> None:
    if not link.exists() and not link.is_symlink():
        link.symlink_to(target)


def _foreign_marker(platform: str) -> str:
    return "bin" if platform == "windows" else "Scripts"


def unirig_dir(repo: Path, platform: str) -> Path:
    default = repo / "vendor/UniRig"
    if (default / ".venv" / _foreign_marker(platform)).exists():
        return repo / ("vendor/UniRig-windows" if platform == "windows" else "vendor/UniRig-linux")
    return default


def engine_allowed(repo: Path, platform: str) -> bool:
    data = tomllib.loads((repo / "engine/pyproject.toml").read_bytes().decode("utf-8"))
    environments = data.get("tool", {}).get("uv", {}).get("environments")
    if environments is None:
        return True
    return any(SYSTEM_MARKERS[platform] in str(entry) for entry in environments)


def foreign_env_problems(repo: Path, platform: str) -> list[str]:
    marker = _foreign_marker(platform)
    system = "Linux" if platform == "windows" else "Windows"
    problems = []
    for project in ("engine", "client"):
        folder = f"{project}/.venv"
        if (repo / folder / marker).exists():
            problems.append(
                f"{folder} is a {system} environment ({folder}/{marker} exists). This script will not delete it: "
                f"move or remove {folder} by hand, then run this script again."
            )
    return problems


def _gpu_problems(probes: Probes) -> list[str]:
    try:
        output = probes.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    except (OSError, subprocess.SubprocessError):
        return [NO_DRIVER]
    return [] if output.strip() else [NO_DRIVER]


def preflight(repo: Path, platform: str, probes: Probes) -> list[str]:
    problems: list[str] = []
    if platform != "macos":
        problems += _gpu_problems(probes)
        free = probes.disk_free(repo)
        if free < MIN_FREE_BYTES:
            problems.append(
                f"Only {free / 10**9:.0f} GB free next to the repository; the install needs at least "
                f"{MIN_FREE_BYTES // 10**9} GB"
            )
    return problems + foreign_env_problems(repo, platform)


def site_packages(venv: Path, platform: str) -> Path:
    if platform == "windows":
        return venv / "Lib/site-packages"
    return venv / "lib/python3.11/site-packages"


def venv_python(venv: Path, platform: str) -> Path:
    if platform == "windows":
        return venv / "Scripts/python.exe"
    return venv / "bin/python"


def build_env(repo: Path, options: Options) -> dict[str, str]:
    cuda_home = CUDA_DIR / "home"
    return {
        "CUDA_HOME": str(cuda_home),
        "PATH": str(cuda_home / "bin"),
        "TORCH_CUDA_ARCH_LIST": "12.0",
        "MAX_JOBS": str(max(1, min(options.cpu_count, 8))),
        "UV_LINK_MODE": "copy",
        "HF_HOME": str(repo / "data/hf"),
        "U2NET_HOME": str(repo / "data/u2net"),
        "HF_TOKEN_PATH": str(options.home / TOKEN_RELATIVE),
    }


def run_retried(ctx: Context, command: Command) -> None:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            ctx.run(command)
        except CommandFailed as error:
            if attempt == RETRY_ATTEMPTS:
                raise
            ctx.log(f"Attempt {attempt} failed, retrying in {RETRY_PAUSE_S:.0f} s: {error}")
            time.sleep(RETRY_PAUSE_S)
        else:
            return


def git_fetch_pinned(ctx: Context, folder: Path, url: str, commit: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for argv in (
        ["git", "init"],
        ["git", "config", "remote.origin.url", url],
        ["git", "fetch", "--depth", "1", "origin", commit],
        ["git", "checkout", "FETCH_HEAD"],
        ["git", "submodule", "update", "--init", "--recursive", "--depth", "1"],
    ):
        ctx.run(Command(argv, cwd=folder))


def fetch_extension(ctx: Context, extensions: Path, name: str, url: str, commit: str) -> None:
    target = extensions / name
    if target.exists():
        return
    partial = extensions / f"{name}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    git_fetch_pinned(ctx, partial, url, commit)
    os.replace(partial, target)


def cuda_toolkit_step(probes: Probes, env: dict[str, str]) -> Step:
    home = CUDA_DIR / "home"
    pkgs = CUDA_DIR / "pkgs"
    cu13 = pkgs / "nvidia/cu13"

    def done() -> bool:
        try:
            complete = (home / "include/cusparse.h").is_file()
            return complete and CUDA_VERSION in probes.run([str(home / "bin/nvcc"), "--version"])
        except (OSError, subprocess.SubprocessError):
            return False

    def run(ctx: Context) -> None:
        ctx.run(Command(["uv", "pip", "install", "--python", "3.11", "--target", str(pkgs), *CUDA_PACKAGES], env=env))
        CUDA_DIR.mkdir(parents=True, exist_ok=True)
        _link(home, str(cu13))
        _link(cu13 / "lib64", "lib")
        _link(cu13 / "lib" / "libcudart.so", "libcudart.so.13")

    return Step("cuda-toolkit", f"CUDA {CUDA_VERSION} toolkit", done, run)


def hunyuan_step(repo: Path) -> Step:
    hunyuan = repo / "vendor/Hunyuan3D-2.1"
    archive = repo / "data/downloads" / f"hunyuan-{HUNYUAN_COMMIT}.tar.gz"

    def done() -> bool:
        return (hunyuan / "hy3dshape").is_dir() and (hunyuan / "hy3dpaint/ckpt/RealESRGAN_x4plus.pth").is_file()

    def run(ctx: Context) -> None:
        step = ctx.step_context()
        download(HUNYUAN_TARBALL, archive, step)
        extract_tarball(archive, hunyuan, step)
        download(REALESRGAN_URL, hunyuan / "hy3dpaint/ckpt/RealESRGAN_x4plus.pth", step)
        apply_patch(repo / PATCH, hunyuan, step)

    return Step("hunyuan-source", "Hunyuan3D 2.1 source", done, run)


def engine_env_step(repo: Path, platform: str, env: dict[str, str]) -> Step:
    engine = repo / "engine"

    def done() -> bool:
        return _exists(site_packages(engine / ".venv", platform), "custom_rasterizer*")

    def run(ctx: Context) -> None:
        ctx.run(Command(
            ["uv", "sync", "--frozen", "--no-dev", "--no-install-package", "basicsr",
             "--no-install-package", "realesrgan", "--no-install-package", "custom-rasterizer"],
            cwd=engine, env=env,
        ))
        ctx.run(Command(["uv", "sync", "--frozen", "--no-dev"], cwd=engine, env=env))

    return Step("engine-env", "Engine environment", done, run)


def inpaint_step(repo: Path, platform: str, env: dict[str, str]) -> Step:
    renderer = repo / "vendor/Hunyuan3D-2.1/hy3dpaint/DifferentiableRenderer"
    python = venv_python(repo / ENGINE_VENV, platform)

    def done() -> bool:
        return _exists(renderer, "mesh_inpaint_processor*.so") or _exists(renderer, "mesh_inpaint_processor*.pyd")

    def run(ctx: Context) -> None:
        ctx.run(Command([str(python), "-c", INPAINT_SCRIPT], cwd=renderer, env=env))

    return Step("inpaint", "Mesh inpainting module", done, run)


def weights_done(repo: Path) -> bool:
    hf = repo / "data/hf"
    return (
        _exists(hf, "hub/models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-dit-v2-1")
        and _exists(hf, "hub/models--facebook--dinov2-giant/snapshots/*/model.safetensors")
        and (repo / "data/u2net/birefnet-general.onnx").is_file()
    )


def weights_step(repo: Path, python: Path, env: dict[str, str]) -> Step:
    hf = repo / "data/hf"

    def run(ctx: Context) -> None:
        run_retried(ctx, Command([str(python), "-c", WEIGHTS_SCRIPT], cwd=repo, env=env))
        for path in hf.glob("hub/models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-paintpbr-v2-1/unet/attn_processor.py"):
            patch_file(repo / PATCH, path, ctx.step_context())

    return Step("weights", "Model weights", lambda: weights_done(repo), run)


def weights_only_step(repo: Path, env: dict[str, str]) -> Step:
    def run(ctx: Context) -> None:
        run_retried(ctx, Command(
            ["uv", "run", "--no-project", "--python", "3.11", "--with", "huggingface-hub==0.30.2",
             "--with", "rembg==2.0.65", "--with", "onnxruntime", "python", "-c", WEIGHTS_SCRIPT],
            cwd=repo, env=env,
        ))

    return Step("weights-only", "Model weights", lambda: weights_done(repo), run)


def engine_blocked_step() -> Step:
    def run(ctx: Context) -> None:
        raise Blocked(ENGINE_BLOCKED)

    return Step("engine-blocked", "Engine on Windows", lambda: False, run)


def rig_steps(repo: Path, platform: str, env: dict[str, str]) -> list[Step]:
    unirig = unirig_dir(repo, platform)
    venv = unirig / ".venv"
    python = venv_python(venv, platform)
    requirements = repo / LOG_DIR / "unirig-requirements.txt"
    archive = repo / "data/downloads" / f"unirig-{UNIRIG_COMMIT}.tar.gz"
    flash_env = {**env, "FLASH_ATTN_CUDA_ARCHS": "120", "NVCC_THREADS": "4"}

    def pip(*args: str, step_env: dict[str, str] | None = None) -> Command:
        return Command(["uv", "pip", "install", "--python", ".venv", *args], cwd=unirig, env=step_env or env)

    def fetch(ctx: Context) -> None:
        step = ctx.step_context()
        download(UNIRIG_TARBALL, archive, step)
        extract_tarball(archive, unirig, step)

    def write_requirements() -> None:
        lines = (unirig / "requirements.txt").read_text(encoding="utf-8").splitlines()
        requirements.parent.mkdir(parents=True, exist_ok=True)
        requirements.write_text("\n".join(line for line in lines if "flash_attn" not in line) + "\n", encoding="utf-8")

    def install_env(ctx: Context) -> None:
        if not python.exists():
            ctx.run(Command(["uv", "venv", "--python", "3.11", ".venv"], cwd=unirig, env=env))
        ctx.run(pip("torch==2.9.1", "torchvision==0.24.1", "--index-url", PYTORCH_INDEX))
        write_requirements()
        ctx.run(pip("-r", str(requirements), "spconv-cu126", "numpy==1.26.4", "ninja", "packaging",
                    "psutil", "setuptools", "wheel"))
        ctx.run(pip("torch_scatter", "torch_cluster", "-f", PYG_WHEELS))
        ctx.run(pip("--no-build-isolation", "flash-attn", step_env=flash_env))

    def fetch_weights(ctx: Context) -> None:
        run_retried(ctx, Command([str(python), "-c", UNIRIG_WEIGHTS_SCRIPT], cwd=repo, env=env))

    def weights_done() -> bool:
        snapshots = repo / "data/hf/hub/models--VAST-AI--UniRig/snapshots"
        checkpoints = any(
            all((snapshot / name).is_file() for name in UNIRIG_CHECKPOINTS) for snapshot in snapshots.glob("*")
        )
        return checkpoints and _exists(repo / "data/hf", "hub/models--facebook--opt-350m/snapshots/*/config.json")

    return [
        Step("unirig-source", "UniRig source", lambda: (unirig / "run.py").is_file(), fetch),
        Step("unirig-env", "UniRig environment",
             lambda: _exists(site_packages(venv, platform), "flash_attn*"), install_env),
        Step("unirig-weights", "UniRig weights", weights_done, fetch_weights),
    ]


def trellis_source_done(trellis: Path) -> bool:
    return (trellis / "trellis2").is_dir() and all(
        (trellis / "extensions" / name).is_dir() for name, _, _ in EXTENSIONS
    )


def trellis_steps(repo: Path, env: dict[str, str], probes: Probes) -> list[Step]:
    trellis = repo / TRELLIS_DIR
    venv = trellis / ".venv"
    python = venv_python(venv, "linux")

    def source(ctx: Context) -> None:
        git_fetch_pinned(ctx, trellis, TRELLIS_URL, TRELLIS_COMMIT)
        for name, url, commit in EXTENSIONS:
            fetch_extension(ctx, trellis / "extensions", name, url, commit)

    def env_install(ctx: Context) -> None:
        if not python.exists():
            ctx.run(Command(["uv", "venv", "--python", "3.11", ".venv"], cwd=trellis, env=env))
        ctx.run(Command(["uv", "pip", "install", "--python", ".venv", *TRELLIS_TORCH,
                         "--index-url", PYTORCH_INDEX], cwd=trellis, env=env))
        ctx.run(Command(["uv", "pip", "install", "--python", ".venv", *TRELLIS_PACKAGES], cwd=trellis, env=env))
        for path in ("./nvdiffrast", "./CuMesh", "./FlexGEMM", "../o-voxel"):
            ctx.run(Command(["uv", "pip", "install", "--python", "../.venv", "--no-build-isolation",
                             "--no-deps", path], cwd=trellis / "extensions", env=env))

    def weights(ctx: Context) -> None:
        run_retried(ctx, Command([str(python), "-c", TRELLIS_WEIGHTS_SCRIPT], cwd=repo, env=env))
        if not probes.has_hf_access(DINOV3_REPO):
            raise Skipped(DINOV3_SKIPPED)
        run_retried(ctx, Command([str(python), "-c", DINOV3_SCRIPT], cwd=repo, env=env))

    return [
        Step("trellis-source", "TRELLIS.2 source", lambda: trellis_source_done(trellis), source),
        Step("trellis-env", "TRELLIS.2 environment",
             lambda: _exists(site_packages(venv, "linux"), "o_voxel*"), env_install),
        Step("trellis-weights", "TRELLIS.2 weights", lambda: trellis_weights_done(repo), weights),
    ]


def trellis_weights_done(repo: Path) -> bool:
    hf = repo / "data/hf/hub"
    return (
        _exists(hf, "models--microsoft--TRELLIS.2-4B/snapshots/*/ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16.safetensors")
        and _exists(hf, "models--microsoft--TRELLIS.2-4B/snapshots/*/ckpts/slat_flow_imgshape2tex_dit_1_3B_1024_bf16.safetensors")
        and _exists(hf, "models--facebook--dinov3-vitl16-pretrain-lvd1689m/snapshots/*/model.safetensors")
    )


def build_steps(repo: Path, platform: str, options: Options, probes: Probes) -> list[Step]:
    if platform == "macos":
        return []
    if platform not in ("linux", "windows"):
        raise ValueError(f"Unknown platform: {platform}")
    env = build_env(repo, options)
    engine_python = venv_python(repo / ENGINE_VENV, platform)
    if platform == "linux":
        steps = [
            cuda_toolkit_step(probes, env),
            hunyuan_step(repo),
            engine_env_step(repo, platform, env),
            inpaint_step(repo, platform, env),
            weights_step(repo, engine_python, env),
        ]
        if options.rig:
            steps += rig_steps(repo, platform, env)
        if options.trellis:
            steps += trellis_steps(repo, env, probes)
        return steps
    steps = [hunyuan_step(repo), weights_only_step(repo, env)]
    if not engine_allowed(repo, platform):
        return steps + [engine_blocked_step()]
    steps += [engine_env_step(repo, platform, env), inpaint_step(repo, platform, env)]
    if options.rig:
        steps += rig_steps(repo, platform, env)
    return steps


def _file_logger(path: Path) -> Callable[[str], None]:
    def log(line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return log


def execute(command: Command, log: Callable[[str], None]) -> None:
    """Run a command, logging its output; the command's PATH is prepended to the inherited one."""
    log("$ " + " ".join(command.argv))
    env = {**os.environ, **command.env}
    env["PATH"] = os.pathsep.join(part for part in (command.env.get("PATH", ""), os.environ.get("PATH", "")) if part)
    process = subprocess.Popen(
        command.argv, cwd=command.cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for line in process.stdout:
        log(line.rstrip("\n"))
    process.wait()
    if process.returncode != 0:
        raise CommandFailed(f"{command.argv[0]} exited with {process.returncode}")


def run_plan(steps: list[Step], dry_run: bool, repo: Path, runner: Runner,
             out: Callable[[str], None]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for index, step in enumerate(steps):
        if step.done():
            results.append((step.id, "done"))
            out(f"{step.id}: " + ("done" if dry_run else "skipped (already done)"))
            continue
        if dry_run:
            results.append((step.id, "would run"))
            out(f"{step.id}: would run")
            continue
        log_path = repo / LOG_DIR / f"{step.id}.log"
        context = Context(repo=repo, log=_file_logger(log_path), runner=runner)
        try:
            step.run(context)
        except Skipped as error:
            results.append((step.id, "skipped"))
            out(f"{step.id}: skipped: {error}")
            continue
        except Exception as error:
            results.append((step.id, "failed"))
            out(f"{step.id}: failed: {error}\n  log: {log_path}")
            results += [(later.id, "not run") for later in steps[index + 1:]]
            return results
        results.append((step.id, "done"))
        out(f"{step.id}: done")
    return results


def start_command(repo: Path, env: Mapping[str, str], platform: str, options: Options, trellis_ready: bool) -> str:
    prefixes = []
    if read_pointer(env) is not None:
        prefixes.append("XDG_CONFIG_HOME=$PWD/data/repo-xdg-config")
    unirig = unirig_dir(repo, platform)
    if unirig != repo / "vendor/UniRig":
        prefixes.append(f"MODEL3D_UNIRIG_DIR=$PWD/{unirig.relative_to(repo).as_posix()}")
    if trellis_ready:
        prefixes.append("MODEL3D_GENERATOR=trellis2")
    prefixes.append("client/.venv/bin/python -m model3d_client")
    return " ".join(prefixes)


def _probe_run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def _hf_access(repo_id: str, token_path: Path) -> bool:
    request = urllib.request.Request(f"https://huggingface.co/{repo_id}/resolve/main/config.json", method="HEAD")
    if token_path.is_file():
        request.add_header("Authorization", f"Bearer {token_path.read_text(encoding='utf-8').strip()}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except OSError:
        return False


def real_probes(token_path: Path) -> Probes:
    return Probes(
        run=_probe_run,
        disk_free=lambda path: shutil.disk_usage(path).free,
        has_hf_access=lambda repo_id: _hf_access(repo_id, token_path),
    )


def main(argv: list[str] | None = None, *, repo: Path | None = None, platform: str | None = None,
         probes: Probes | None = None, runner: Runner = execute) -> int:
    parser = argparse.ArgumentParser(description="Install Model3D's engine, models, UniRig, and TRELLIS.2.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    parser.add_argument("--no-rig", action="store_true", help="skip UniRig")
    parser.add_argument("--no-trellis", action="store_true", help="skip TRELLIS.2")
    args = parser.parse_args(argv)
    repo = repo or Path(__file__).resolve().parents[2]
    system = platform or PLATFORMS.get(sys.platform)
    if system is None:
        print(f"Unsupported system: {sys.platform}", file=sys.stderr)
        return 2
    options = Options(rig=not args.no_rig, trellis=not args.no_trellis)
    probes = probes or real_probes(options.home / TOKEN_RELATIVE)
    problems = preflight(repo, system, probes)
    if problems:
        for problem in problems:
            print(f"- {problem}")
        return 2
    if system == "macos":
        print(MACOS_DONE)
        return 0
    steps = build_steps(repo, system, options, probes)
    results = run_plan(steps, args.dry_run, repo, runner, print)
    if args.dry_run:
        return 0
    print("Summary:")
    for step_id, status in results:
        print(f"  {step_id}: {status}")
    if any(status == "failed" for _, status in results):
        return 1
    if system == "linux":
        trellis = [step for step in steps if step.id.startswith("trellis-")]
        trellis_ready = bool(trellis) and all(step.done() for step in trellis)
        if read_pointer(os.environ) is not None:
            (repo / "data/repo-xdg-config").mkdir(parents=True, exist_ok=True)
        print("Start the app from the repository root with:")
        print(start_command(repo, os.environ, system, options, trellis_ready))
    return 0


if __name__ == "__main__":
    sys.exit(main())
