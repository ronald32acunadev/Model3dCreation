# Model3D

![Platform](https://img.shields.io/badge/platform-Linux%20x86__64-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![CUDA](https://img.shields.io/badge/CUDA-13.0%2B%20(sm__120)-76B900)
![Version](https://img.shields.io/badge/version-0.1.1-informational)

**Model3D** is a local, GPU-only pipeline that turns a single PNG or JPEG image into a textured (and optionally rigged) binary glTF 2.0 (`.glb`) 3D model using **Hunyuan3D 2.1** and **UniRig**. It ships with a FastAPI/WebSocket inference engine, a PySide6 desktop application with an embedded 3D viewer and first-run setup wizard, a native Blender 4.2+ extension, and a Debian (`.deb`) package builder.

Designed for Linux `x86_64` workstations with NVIDIA GPUs (targeting dual RTX 5070 12 GB cards and adaptable to single-GPU setups), Model3D isolates the `shape`, `paint`, and `rig` stages in dedicated worker processes and automatically selects the optimal GPU placement and memory management strategy based on available VRAM.

---

## Table of Contents

- [Key Features](#key-features)
- [How It Works & GPU Run Modes](#how-it-works--gpu-run-modes)
- [Architecture & Repository Layout](#architecture--repository-layout)
- [Prerequisites & Installation](#prerequisites--installation)
- [Usage](#usage)
  - [Desktop Application](#desktop-application)
  - [Local Inference Engine](#local-inference-engine)
  - [Blender Add-on](#blender-add-on)
  - [REST & WebSocket API Reference](#rest--websocket-api-reference)
  - [Environment Variables](#environment-variables)
  - [Diagnostic & Packaging Scripts](#diagnostic--packaging-scripts)
- [Development & Testing](#development--testing)
- [Troubleshooting & Platform Notes](#troubleshooting--platform-notes)
- [License](#license)

---

## Key Features

- **100% Local Image-to-3D Pipeline**: Converts 2D images (`PNG`/`JPEG` up to 20 MB) into 3D meshes (`shape`), generates PBR textures (`paint`), and optionally rigs skeletons (`rig`) into `.glb` assets without cloud dependencies.
- **Standalone Skeletal Rigging**: Accepts existing `.glb` models (up to 200 MB) via `/v1/rig-jobs` to generate armatures and skinning weights using UniRig.
- **Automatic Multi-GPU & Single-GPU Placement**: Probes NVIDIA GPUs and PCIe link bandwidth at startup, assigning stages across two GPUs or dynamically swapping stage workers on a single GPU.
- **Fault-Tolerant Process Workers**: Runs `shape` and `paint` inside isolated `ProcessStageRunner` subprocesses with configurable task timeouts. Each worker loads its model for its task and unloads when no task waits for it, so an idle engine holds no VRAM.
- **PySide6 Desktop Client & Setup Wizard**: Provides an interactive first-run installer (probing drivers, `nvcc`, compilers, and downloading ~31 GB base + ~8 GB skeleton weights with resumable step fingerprints), real-time job progress over WebSockets, a local model library, and an embedded QtWebEngine 3D viewer (`English` and `Español`).
- **Zero-Dependency Blender Extension**: Connects Blender (`>= 4.2.0`) directly to the local engine and shared model library using Python's standard library (`urllib`).

---

## How It Works & GPU Run Modes

When `model3d_engine` starts, `model3d_engine.gpu.probe` and `model3d_engine.gpu.modes` inspect all visible NVIDIA GPUs and select one of five execution modes (using a `512 MiB` free-VRAM safety margin above measured peak usage: `10111 MiB` for `shape` (a `high` job) and `10168 MiB` for `paint`):

| Run Mode | Minimum Free VRAM | Behavior |
| :--- | :--- | :--- |
| `dual_gpu` | Two GPUs (`>= 10.4 GiB` on paint card, `>= 9.2 GiB` on shape card) | Runs `shape` on one GPU and `paint` on the GPU with the most free VRAM (tie-broken by PCIe link bandwidth). `rig` shares the paint GPU. |
| `single_gpu_resident` | One GPU (`>= 17.5 GiB` free) | `shape` and `paint` take turns on the same GPU, one loaded at a time. |
| `single_gpu_swap` | One GPU (`>= 10.4 GiB` free) | Runs `shape` and `paint` sequentially on the same GPU, unloading each worker process between stages to free VRAM. |
| `shape_only` | One GPU (`>= 9.2 GiB` free) | Runs untextured mesh generation (`"texture": false`) and optional rigging on a single GPU when VRAM is insufficient for `paint`. |
| `unsupported` | Below `9.2 GiB` free or no supported GPU | Starts the HTTP API so `/v1/health` can report the hardware diagnostic reason; job submissions return `503 engine_not_ready`. |

### Generation Quality Presets

| Preset (`quality`) | Description |
| :--- | :--- |
| `draft` | Fast preview generation with reduced diffusion steps. |
| `final` | Default balanced production quality (`Quality.final`). |
| `high` | Maximum detail generation. |

With `MODEL3D_GENERATOR=trellis2`, `draft` runs TRELLIS.2 at `512` and `final`/`high` at `1024_cascade`; the face target is the same as Hunyuan's (40 000, or 150 000 for `high`).

### Alternative Generator: TRELLIS.2

`MODEL3D_GENERATOR=trellis2` replaces Hunyuan's shape and paint stages with [TRELLIS.2](https://github.com/microsoft/TRELLIS.2), which generates geometry and PBR texture in one model and gives noticeably sharper faces (measurements in `docs/userstories/alternative-shape-models/`). With two cards it splits each job: decoding and texture baking, the heaviest part (about 10.1 GiB), run on the card with the most free VRAM, and the image encoder and flow models (about 5.2 GiB) run on the other; only the latents cross between them. With one card that has about 10.7 GiB free plus the 512 MiB margin, everything runs there, closer to the limit on dense models. Otherwise the engine starts in `unsupported` with the reason. Jobs and rigging take turns, so only one step uses the GPUs at a time. Its sub-models wait in RAM (about 10 GB) and each moves to a GPU only for its step. `MODEL3D_MODE` and the bus overrides apply only to Hunyuan and are rejected with `trellis2`.

The setup wizard does not install it yet; set it up by hand:

1. Clone it: `git clone --depth 1 --recursive --shallow-submodules https://github.com/microsoft/TRELLIS.2.git vendor/TRELLIS.2`.
2. Create `vendor/TRELLIS.2/.venv` with Python 3.11 and install torch 2.9.0 + torchvision 0.24.0 + xformers 0.0.33.post1 from `https://download.pytorch.org/whl/cu130`, `transformers>=4.56,<5` (5.x breaks its DINOv3 extractor), and the packages from its `setup.sh` "basic" list.
3. Build nvdiffrast v0.4.0, CuMesh, FlexGEMM, and `vendor/TRELLIS.2/o-voxel` with `uv pip install --no-build-isolation --no-deps ./<path>`, with `CUDA_HOME` pointing at CUDA 13.2 and `TORCH_CUDA_ARCH_LIST=12.0`. nvdiffrec is not needed.
4. Weights go to `$MODEL3D_HOME/hf`: `microsoft/TRELLIS.2-4B` (the 512 and 1024 flows and the decoders, about 14 GB), `ckpts/ss_dec_conv3d_16l8_fp16.*` from `microsoft/TRELLIS-image-large`, and `facebook/dinov3-vitl16-pretrain-lvd1689m`. DINOv3 is gated: request access on Hugging Face and log in first. The gated `briaai/RMBG-2.0` is not needed; the engine removes backgrounds with BiRefNet.

---

## Architecture & Repository Layout

The repository is organized as five independent [`uv`](https://docs.astral.sh/uv/) projects, each with its own `pyproject.toml` and test suite:

| Subproject | Package Name & Version | Python Requirement | Role |
| :--- | :--- | :--- | :--- |
| [`contracts/`](./contracts) | `model3d-contracts@0.1.0` | `>=3.11` | Shared Pydantic v2 schemas (`jobs`, `events`, `health`). Editable dependency of `engine` and `client`. |
| [`engine/`](./engine) | `model3d-engine@0.1.0` | `==3.11.*` | FastAPI inference server (`python -m model3d_engine`), GPU probe, stage process runner, job scheduler, and store. |
| [`client/`](./client) | `model3d-client@0.1.1` | `>=3.11` | PySide6 MVVM desktop application (`python -m model3d_client`), first-run setup wizard, engine supervisor, and 3D viewer. |
| [`blender/`](./blender) | `model3d-blender@0.1.0` | `>=3.11` | Blender 4.2+ extension in [`blender/model3d/`](./blender/model3d) (`urllib` client, job tracker, library browser, UI panels). |
| [`packaging/`](./packaging) | `model3d-packaging@0.1.0` | `>=3.11` | `.deb` package builder ([`packaging/deb/build.sh`](./packaging/deb/build.sh)) and package verification tests. |

```text
Model3dCreation/
├── contracts/               # Shared Pydantic models (model3d_contracts)
├── engine/                  # FastAPI GPU server (model3d_engine) & Hunyuan3D patch
│   ├── patches/             # hunyuan3d-paint-device.patch (removes hardcoded cuda:0)
│   └── src/model3d_engine/  # api/, gpu/, jobs/, stages/, config.py, __main__.py
├── client/                  # PySide6 desktop app (model3d_client)
│   ├── launch.sh            # Launches the desktop client via uv from any directory
│   ├── scripts/             # update_translations.sh, test_system_python.sh
│   └── src/model3d_client/  # services/, setup/, viewmodels/, views/, viewer/, i18n/
├── blender/                 # Blender 4.2+ add-on (blender/model3d/) and test suite
├── packaging/               # Debian package build scripts (packaging/deb/build.sh)
├── scripts/                 # GPU verification (verify_gpus.py) & VRAM benchmark (measure_stage_vram.py)
└── Fases/                   # Architectural phase specifications (Spanish)
```

### Runtime & Configuration Paths

| Path | Purpose |
| :--- | :--- |
| `$XDG_CONFIG_HOME/model3d/config.json` (fallback `~/.config/model3d/config.json`) | Pointer file written by the setup wizard naming the active installation directory. |
| `<install_or_repo>/install.json` | Fingerprinted step state recorded by the setup wizard for resume/repair. |
| `$MODEL3D_HOME` (default `<install_or_repo>/data`) | Root runtime data directory shared by the engine and desktop client. |
| `$MODEL3D_HOME/jobs/` | Persistent job records, input files, and generated `model.glb` outputs. |
| `$MODEL3D_HOME/library/` | Saved 3D models managed by the desktop app and browsable from Blender. |
| `$MODEL3D_HOME/hf/` | Local Hugging Face cache (`tencent/Hunyuan3D-2.1`, `facebook/dinov2-giant`, `VAST-AI/UniRig`, `facebook/opt-350m`). |
| `$MODEL3D_HOME/logs/engine.log` | Engine log written when managed by the desktop client's `EngineSupervisor`. |

---

## Prerequisites & Installation

### Hardware & System Requirements

- **OS**: Linux `x86_64` (Ubuntu 26.04 target for `.deb` system packages).
- **GPU**: NVIDIA GPU(s) with compute capability `sm_120` (e.g., GeForce RTX 5070 12 GB) and NVIDIA driver supporting CUDA 13.
- **Build Toolchain**: CUDA Toolkit 13 (`nvcc`), `build-essential` (`c++`), and `patch` (required to compile `custom_rasterizer`, `mesh_inpaint_processor`, and `flash-attn`).
- **Disk Space**: ~31 GB for the base installation (Hunyuan3D 2.1 + DINOv2 + RealESRGAN + BiRefNet weights) plus ~8 GB for optional skeletal rigging (UniRig).

### Option 1: Build and Install the `.deb` Package

Build the `.deb` package from the repository root:

```bash
packaging/deb/build.sh
sudo apt install ./dist/model3d_0.1.1_amd64.deb
```

Launch `model3d` from your application menu or terminal. On first run, the **Setup Wizard** verifies your NVIDIA driver, GPUs, and `nvcc`, then downloads and builds the engine environment and model weights inside your chosen installation directory.

### Option 2: Development Setup from Source

1. **Install `uv`** and ensure Python 3.11 and CUDA 13 (`nvcc`) are available.
2. **Clone external vendor repositories** (not tracked in git) when setting up the engine manually without the desktop wizard:
   - Clone `Tencent-Hunyuan/Hunyuan3D-2.1` into `vendor/Hunyuan3D-2.1` and apply the device placement patch:
     ```bash
     git apply --directory=vendor/Hunyuan3D-2.1 engine/patches/hunyuan3d-paint-device.patch
     ```
   - Download `RealESRGAN_x4plus.pth` to `vendor/Hunyuan3D-2.1/hy3dpaint/ckpt/RealESRGAN_x4plus.pth`.
   - Build `mesh_inpaint_processor` inside `vendor/Hunyuan3D-2.1/hy3dpaint/DifferentiableRenderer`.
   - *(Optional for rigging)* Set up `VAST-AI-Research/UniRig` in `vendor/UniRig` with its own `.venv`.
   - Alternatively, launch the desktop client and let the **Setup Wizard** perform all downloads, patches, native builds, and weight fetches automatically.
3. **Sync subproject environments**:
   ```bash
   (cd contracts && uv sync)
   (cd client && uv sync)
   (cd engine && uv sync)
   ```

---

## Usage

### Desktop Application

Start the desktop client (which automatically starts and supervises the local engine if it is not already running):

```bash
# Using the launcher script from anywhere
./client/launch.sh

# Or directly via uv from the repository root
uv run --project client python -m model3d_client
```

**Export formats.** **Export…** saves the selected library model as GLB (a plain copy) or converts it with the engine's `bpy` in a separate process (no GPU or running engine needed). The dialog warns when the chosen format drops the skeleton or the texture.

| Format | Texture | Skeleton | Files written |
| :--- | :--- | :--- | :--- |
| GLB | Yes | Yes | 1 |
| FBX | Yes (embedded) | Yes | 1 |
| OBJ + MTL | Yes | No | `.obj`, `.mtl`, texture images |
| STL | No | No | 1 |
| USDZ | Yes (packaged) | Viewer-dependent | 1 |

Exports keep a model's animation in GLB and FBX.

**Walk animation.** For a model with a skeleton, the library's context menu offers **Add walk animation**. The app detects the body type from the skeleton (2 legs: biped, 4: quadruped; otherwise you choose), lets you confirm or change it, and saves the result as a new `<name> (walk)` entry with a looping one-second `Walk` clip. The viewer plays it, with a play/pause button. The walk is procedural: UniRig's bones have no semantic names, so legs and arms are found by their position and hierarchy. It is a way to test the rig, not production animation, and skinning flaws from UniRig show up when the model moves.

### Local Inference Engine

Run the FastAPI engine standalone (listens on `127.0.0.1:8765` by default):

```bash
uv run --project engine python -m model3d_engine
```

### Blender Add-on

The Blender extension is located in [`blender/model3d/`](./blender/model3d) (or packaged at `/opt/model3d/blender/model3d.zip` when installed via `.deb`).
- Requires **Blender 4.2.0 or newer**.
- Install `model3d.zip` in Blender under **Edit → Preferences → Add-ons → Install from Disk**, then use the **Model3D** sidebar panel to generate textured/rigged meshes from images, rig existing objects, or import models from the local Model3D library.

### REST & WebSocket API Reference

The engine binds strictly to loopback addresses and rejects non-loopback `Host` headers (`LoopbackGuard`).

| Method & Path | Description | Key Parameters / Payload |
| :--- | :--- | :--- |
| `GET /v1/health` | Returns engine readiness, active run mode, per-GPU VRAM usage, and rigging availability. | None |
| `POST /v1/jobs` | Submits a `PNG` or `JPEG` image (max 20 MB) for 3D generation. Returns `202 Accepted` with a `Job` object. | `multipart/form-data`: `image` (file), `options` (JSON string matching `JobOptions`) |
| `POST /v1/rig-jobs` | Submits an existing `.glb` file (max 200 MB) for standalone UniRig skeletal rigging. Returns `202 Accepted`. | `multipart/form-data`: `model` (`.glb` file) |
| `GET /v1/jobs/{job_id}` | Returns current job state (`queued`, `shaping`, `painting`, `rigging`, `succeeded`, `failed`, `cancelled`), progress (`0.0`–`1.0`), and resource links. | Path param: `job_id` |
| `GET /v1/jobs/{job_id}/result` | Downloads the completed `model.glb` (`model/gltf-binary`). Returns `409 job_not_finished` if still running. | Path param: `job_id` |
| `DELETE /v1/jobs/{job_id}` | Cancels a queued or running job. Returns `202 Accepted`. | Path param: `job_id` |
| `WS /v1/jobs/{job_id}/events` | Streams real-time JSON `JobEvent` updates until the job reaches a terminal state. | Path param: `job_id` |

#### `JobOptions` JSON Schema (`POST /v1/jobs`)

```json
{
  "texture": true,
  "quality": "final",
  "seed": 1234,
  "remove_background": true,
  "target_faces": 50000,
  "rig": false
}
```

- `quality`: `"draft"` | `"final"` | `"high"` (default: `"final"`).
- `seed`: Integer in `[0, 2^32 - 1]` or `null` for random seed.
- `target_faces`: Integer `>= 1000` or `null` for default face count.

### Environment Variables

All engine and client path overrides are configured via environment variables (`model3d_engine.config.load_settings` and `model3d_client.paths.app_paths`):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MODEL3D_HOME` | `<repo_or_install>/data` | Data directory storing `jobs/`, `library/`, `hf/`, `logs/`, and `settings.json`. |
| `MODEL3D_HUNYUAN_DIR` | `<repo_or_install>/vendor/Hunyuan3D-2.1` | Path to the patched Hunyuan3D 2.1 checkout. |
| `MODEL3D_UNIRIG_DIR` | `<repo_or_install>/vendor/UniRig` | Path to the UniRig checkout containing its `.venv`. |
| `MODEL3D_HOST` | `127.0.0.1` | Loopback address to bind (`127.0.0.1`, `::1`, or `localhost`). Non-loopback hosts exit with code `2`. |
| `MODEL3D_PORT` | `8765` | TCP port for the FastAPI server. |
| `MODEL3D_MODE` | *(auto-detected)* | Force a run mode: `dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, or `shape_only`. |
| `MODEL3D_SHAPE_BUS` | *(auto-detected)* | Explicit PCI bus ID (e.g., `0000:01:00.0`) for the `shape` worker. Must be set together with `MODEL3D_PAINT_BUS`. |
| `MODEL3D_PAINT_BUS` | *(auto-detected)* | Explicit PCI bus ID (e.g., `0000:29:00.0`) for the `paint` worker. Must be set together with `MODEL3D_SHAPE_BUS`. |
| `MODEL3D_ALLOWED_ORIGINS` | `""` (empty) | Comma-separated list of browser `Origin` URLs permitted to send `POST` or `DELETE` requests. |
| `MODEL3D_GENERATOR` | `hunyuan` | Generation pipeline: `hunyuan` (Hunyuan3D shape + paint) or `trellis2` (TRELLIS.2, see above). Other values exit at startup. |
| `MODEL3D_TRELLIS_DIR` | `<repo_or_install>/vendor/TRELLIS.2` | Path to the TRELLIS.2 checkout containing its `.venv`. |
| `MODEL3D_STAGE_TIMEOUT_S` | `900` | Maximum seconds a single stage task may run before its worker process is killed; the next task loads it again. |

### Diagnostic & Packaging Scripts

| Command | Arguments / Env | Description |
| :--- | :--- | :--- |
| `uv run --project engine python scripts/verify_gpus.py` | None | Validates PyTorch CUDA 13 build, `sm_120` kernel support, GPU count/VRAM, `nvidia-smi` PCI bus ordering, `nvcc` version match, `custom_rasterizer`, and `mesh_inpaint_processor`. |
| `uv run --project engine python scripts/measure_stage_vram.py` | `[--bus 0000:29:00.0]` | Benchmarks idle, loaded, and peak VRAM (MiB) and runtime across `final` and `draft` presets on a target GPU. |
| `packaging/deb/build.sh` | `[--out DIR]`, `UV_BINARY=/path/to/uv` | Builds `model3d_<version>_amd64.deb` (defaults to `dist/`). |
| `client/scripts/update_translations.sh` | None | Extracts UI strings with `pyside6-lupdate` into `model3d_es.ts` and compiles `model3d_es.qm` with `pyside6-lrelease`. |
| `client/scripts/test_system_python.sh` | `[pytest args...]` | Runs the `client` test suite against `/usr/bin/python3` and apt-installed `PySide6`/`pydantic`, matching the `.deb` runtime environment. |

---

## Development & Testing

By default, each subproject's `pytest` configuration excludes hardware-dependent tests (`gpu`, `webengine`, `blender`) so unit and integration tests run fast and deterministically on any machine:

```bash
# Run unit & fast integration tests per project
uv run --project contracts pytest
uv run --project engine pytest
uv run --project client pytest
uv run --project blender pytest
uv run --project packaging pytest
```

### Running Hardware & Integration Marker Suites

```bash
# Engine & Client GPU acceptance tests (requires NVIDIA GPUs + full engine environment)
uv run --project engine pytest -m gpu
uv run --project client pytest -m gpu

# Client QtWebEngine viewer tests (requires an active display with GPU rendering)
uv run --project client pytest -m webengine

# Blender smoke tests (requires `blender` executable on PATH)
uv run --project blender pytest -m blender
```

---

## Troubleshooting & Platform Notes

- **`gpu_busy` / Insufficient Free VRAM**: Close or unload local LLM servers (such as LM Studio or Ollama) before starting the engine, running the setup wizard verification step, or executing `scripts/measure_stage_vram.py`.
- **`CUDA_DEVICE_ORDER` Mismatch**: The engine sets `CUDA_DEVICE_ORDER=PCI_BUS_ID` so PyTorch device indices match `nvidia-smi` PCI bus ordering.
- **Pinned Engine Dependencies**: `engine/pyproject.toml` pins specific versions required by upstream native modules (`Python ==3.11.*` for `bpy==4.5.*` wheels, `numpy==1.26.4`, `trimesh==4.4.7` for positional quadric decimation, `onnxruntime>=1.20` for `glibc >= 2.41` non-executable stack compatibility, and `setuptools<81` for `pkg_resources`). Check the inline comments in [`engine/pyproject.toml`](./engine/pyproject.toml) before modifying dependency versions.

---

## License

The Blender add-on ([`blender/model3d/blender_manifest.toml`](./blender/model3d/blender_manifest.toml)) is licensed under `SPDX:GPL-3.0-or-later`.
