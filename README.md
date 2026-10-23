# Model3D

[English](README.md) · [Español](README.es.md)

[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.9.1%20cu130-EE4C2C.svg)](https://pytorch.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.12.0-41CD52.svg)](https://wiki.qt.io/Qt_for_Python)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Blender](https://img.shields.io/badge/Blender-4.2%2B-F5792A.svg)](https://www.blender.org/)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue.svg)](blender/model3d/blender_manifest.toml)

**Model3D** is a local, dual-GPU pipeline that turns single 2D images into textured, rigged 3D models (`.glb`) using **Hunyuan3D 2.1** and **UniRig**. The project is built for dual NVIDIA RTX 5070 workstations on Linux x86_64, featuring a decoupled asynchronous compute engine, a desktop GUI application (PySide6), and an integrated Blender 4.2+ extension add-on.

---

## Table of Contents

- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Repository Structure](#repository-structure)
- [Hardware Target & GPU Modes](#hardware-target--gpu-modes)
- [Prerequisites & Toolchain](#prerequisites--toolchain)
- [Vendor Dependencies & Patches](#vendor-dependencies--patches)
- [Installation & Quick Start](#installation--quick-start)
  - [1. Setting Up Subprojects](#1-setting-up-subprojects)
  - [2. Running the Engine](#2-running-the-engine)
  - [3. Running the Desktop Application](#3-running-the-desktop-application)
  - [4. Installing the Blender Add-on](#4-installing-the-blender-add-on)
- [Configuration Reference](#configuration-reference)
- [REST & WebSocket API Reference](#rest--websocket-api-reference)
- [Diagnostics & Benchmarking Scripts](#diagnostics--benchmarking-scripts)
- [Testing & Quality Assurance](#testing--quality-assurance)
- [License](#license)

---

## Key Features

- **Decoupled Engine-Client Architecture**: Heavy GPU PyTorch inference runs in an independent background process (`model3d_engine`), keeping the UI thread responsive.
- **Dual-GPU Pipeline Acceleration**: Automatically allocates shape generation (Hunyuan3D Stage 1) to the resident GPU card and texture synthesis (Hunyuan3D Stage 2) plus skeletal rigging (UniRig) to the secondary card.
- **Automatic Fallback Modes**: Intelligently adapts to available hardware (`dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, `shape_only`, `unsupported`).
- **Interactive Desktop Client**: Modern PySide6 application with MVVM architecture, automatic engine supervisor, Spanish/English localization, and an integrated QtWebEngine 3D viewport.
- **Native Blender 4.2+ Add-on**: Integrated extension for Blender allowing direct generation, job tracking, library browsing, and auto-rigging directly into scenes with zero external dependencies (standard-library `urllib`).

---

## System Architecture

```text
+-----------------------------------------------------------------------------------+
|                                  CLIENT LAYER                                     |
|                                                                                   |
|  +-------------------------------------+   +-----------------------------------+  |
|  |     PySide6 Desktop Client          |   |       Blender 4.2+ Add-on         |  |
|  |   - MVVM (Views/ViewModels)        |   |   - Native Extension (urllib)     |  |
|  |   - EngineSupervisor               |   |   - Scene Viewport Importer       |  |
|  |   - QtWebEngine 3D Preview          |   |   - Rig & Texture Controls        |  |
|  +-------------------------------------+   +-----------------------------------+  |
+---------------------------|----------------------------------|--------------------+
                            | HTTP (REST) & WebSockets         | HTTP (REST)
                            v                                  v
+-----------------------------------------------------------------------------------+
|                         LOCAL INFERENCE ENGINE (FastAPI)                          |
|                                                                                   |
|  +--------------------+   +-----------------------+   +------------------------+  |
|  |  LoopbackGuard &   |   |  JobStore & EventBus  |   |    Multi-Stage         |  |
|  |  API Endpoints     |-->|  - File-backed jobs   |-->|    Pipelined           |  |
|  |  (/v1/jobs, etc.)  |   |  - Live status stream |   |    Scheduler           |  |
|  +--------------------+   +-----------------------+   +------------------------+  |
|                                                                   |               |
|         +---------------------------------------------------------+               |
|         |                                                         |               |
|         v (GPU Placement)                                         v               |
|  +------------------------------------+   +------------------------------------+  |
|  |       GPU A (Primary Link)         |   |       GPU B (Secondary Link)       |  |
|  |   Shape Worker (Hunyuan3D S1)      |   |   Paint Worker (Hunyuan3D S2)      |  |
|  |   Geometry & Mesh Generation       |   |   PBR Textures & UniRig Rigging    |  |
|  +------------------------------------+   +------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## Repository Structure

The workspace is structured into independent `uv` projects with explicit boundaries:

| Directory | Role | Description |
| :--- | :--- | :--- |
| [`contracts/`](contracts/) | Shared Models | Pydantic v2 schemas (`Job`, `JobOptions`, `JobState`, `Health`, `JobError`) shared by engine and client. |
| [`engine/`](engine/) | Inference Backend | FastAPI server (`python -m model3d_engine`), running on Python 3.11, CUDA 13.0 PyTorch, and `bpy` wheels. |
| [`client/`](client/) | Desktop Application | PySide6 desktop GUI (`python -m model3d_client`), QtWebEngine viewer, and background engine supervisor. |
| [`blender/`](blender/) | Blender Add-on | Blender 4.2+ extension in `blender/model3d/` with Python test harness. |
| [`scripts/`](scripts/) | Tooling & Diagnostics | Hardware verification (`verify_gpus.py`) and stage VRAM benchmarking (`measure_stage_vram.py`). |
| [`Fases/`](Fases/) | Architecture Specs | Phased architectural and technical design specifications (Spanish). |

---

## Hardware Target & GPU Modes

The pipeline is tuned for dual **NVIDIA RTX 5070 (12 GB VRAM each)** running on Linux x86_64.

At startup, the engine queries `nvidia-smi` and link topology, selecting one of five execution modes:

| Mode | Description | GPU Requirement |
| :--- | :--- | :--- |
| `dual_gpu` | Shape stays resident on one card; Paint and UniRig run on the other. Zero swapping overhead. | 2 usable NVIDIA GPUs with >= 10 GiB free VRAM each. |
| `single_gpu_resident` | Both shape and paint models remain loaded in memory concurrently. | 1 GPU with >= 22 GiB free VRAM. |
| `single_gpu_swap` | Shape and paint workers cycle on a single device, unloading weights between stages. | 1 GPU with 10–21 GiB free VRAM. |
| `shape_only` | Geometry generation only; texture painting is disabled. | 1 GPU with 10–12 GiB free VRAM. |
| `unsupported` | Engine boots into error state; API reports failure reasons via `/v1/health`. | Insufficient VRAM (< 10 GiB) or no CUDA GPUs detected. |

---

## Prerequisites & Toolchain

- **Operating System**: Linux x86_64 (e.g., Ubuntu 24.04 / 26.04 LTS)
- **NVIDIA Driver**: 570+ (supporting CUDA 12.8 / 13.0+)
- **Python**: Python 3.11 (strictly required by `engine/` due to `bpy==4.5.*` cp311 wheels)
- **Package Manager**: [`uv`](https://docs.astral.sh/uv/) (>= 0.5.0)
- **Compilers**: `gcc`, `g++`, `ninja`, and `nvcc` matching `CUDA_HOME` for compiled rasterizers.

---

## Vendor Dependencies & Patches

Two upstream subprojects are vendored in `vendor/` (untracked in git):

1. **Hunyuan3D 2.1**:
   ```bash
   git clone https://github.com/Tencent/Hunyuan3D-2.1 vendor/Hunyuan3D-2.1
   # Apply patch to remove hardcoded cuda:0 devices:
   git -C vendor/Hunyuan3D-2.1 apply ../../engine/patches/hunyuan3d-paint-device.patch
   ```
2. **UniRig**:
   ```bash
   git clone https://github.com/VAST-AI-Research/UniRig vendor/UniRig
   # Configure UniRig's dedicated virtual environment inside its vendor folder
   ```

---

## Installation & Quick Start

### 1. Setting Up Subprojects

Synchronize dependencies for each workspace component using `uv`:

```bash
# Shared contracts
cd contracts && uv sync && cd ..

# Client desktop application
cd client && uv sync && cd ..

# Blender add-on test environment
cd blender && uv sync && cd ..

# Engine (requires CUDA_HOME and Hunyuan3D-2.1 clone for custom_rasterizer)
cd engine && uv sync && cd ..
```

### 2. Running the Engine

Start the local API engine directly:

```bash
uv run --project engine python -m model3d_engine
```

The engine initializes on `http://127.0.0.1:8765`. On startup, it inspects GPU topology and loads weights in the background while `/v1/health` reports status.

### 3. Running the Desktop Application

Launch the desktop UI using `uv` or the standalone wrapper script:

```bash
# Direct run via uv
uv run --project client python -m model3d_client

# Or via launch script (used by .desktop launcher)
./client/launch.sh
```

> **Note**: If the engine is not already running, the client's `EngineSupervisor` will automatically spawn `engine/.venv/bin/python -m model3d_engine` and terminate it upon application exit.

### 4. Installing the Blender Add-on

`blender/model3d` is a native Blender 4.2+ extension:

1. Open Blender 4.2 or newer.
2. Go to **Edit** > **Preferences** > **Add-ons / Extensions**.
3. Install from disk or symlink `blender/model3d` into your Blender extensions directory:
   ```bash
   ln -s "$(pwd)/blender/model3d" "$HOME/.config/blender/4.2/extensions/user_default/model3d"
   ```
4. Enable the **Model3D** add-on in the 3D Viewport sidebar (`N`-panel under `Model3D`).

---

## Configuration Reference

The engine is configured strictly through environment variables:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `MODEL3D_HOME` | Path | `data/` | Base directory for job storage (`$MODEL3D_HOME/jobs`) and HuggingFace cache (`$MODEL3D_HOME/hf`). |
| `MODEL3D_HUNYUAN_DIR` | Path | `vendor/Hunyuan3D-2.1` | Root path to the Hunyuan3D 2.1 repository clone. |
| `MODEL3D_UNIRIG_DIR` | Path | `vendor/UniRig` | Root path to the UniRig repository clone. |
| `MODEL3D_HOST` | String | `127.0.0.1` | Engine HTTP and WebSocket binding address. |
| `MODEL3D_PORT` | Integer | `8765` | Engine port. |
| `MODEL3D_MODE` | String | `None` (auto) | Forced execution mode: `dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, or `shape_only`. |
| `MODEL3D_SHAPE_BUS` | String | `None` (auto) | PCI bus ID for shape worker (e.g., `0000:29:00.0`). Set with `MODEL3D_PAINT_BUS`. |
| `MODEL3D_PAINT_BUS` | String | `None` (auto) | PCI bus ID for paint/rig worker (e.g., `0000:01:00.0`). Set with `MODEL3D_SHAPE_BUS`. |
| `MODEL3D_ALLOWED_ORIGINS`| String | `""` | Comma-separated list of allowed browser origins for CORS/CSRF protection. |
| `MODEL3D_STAGE_TIMEOUT_S`| Float | `900.0` | Timeout in seconds for an individual stage before killing the worker process. |

---

## REST & WebSocket API Reference

The engine provides an HTTP + WebSocket API with loopback and DNS-rebinding protection (`LoopbackGuard`):

### Endpoints

| Method | Endpoint | Description | Status Codes |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/health` | Engine readiness, chosen GPU mode, and device memory status. | `200 OK` |
| `POST` | `/v1/jobs` | Submit a new image-to-3D generation job (`multipart/form-data`: `image`, `options`). | `202 Accepted`, `400`, `413`, `503` |
| `POST` | `/v1/rig-jobs` | Submit an existing `.glb` model for automatic skeletal rigging (`multipart/form-data`: `model`). | `202 Accepted`, `400`, `413`, `503` |
| `GET` | `/v1/jobs/{job_id}` | Retrieve current state, progress, and stage details of a job. | `200 OK`, `404 Not Found` |
| `GET` | `/v1/jobs/{job_id}/result`| Download the generated or rigged binary `model.glb`. | `200 OK`, `404`, `409 Conflict` |
| `DELETE` | `/v1/jobs/{job_id}` | Cancel a queued or running job. | `202 Accepted`, `404`, `409` |
| `WS` | `/v1/jobs/{job_id}/events` | Real-time WebSocket stream of progress, state transitions, and errors. | Connection upgrade |

### Job Options (`options` JSON in `POST /v1/jobs`)

```json
{
  "texture": true,
  "quality": "final",
  "seed": 42,
  "remove_background": true,
  "target_faces": 40000,
  "rig": false
}
```

- **`quality`**: `"draft"`, `"final"`, or `"high"`.
- **`target_faces`**: Integer >= 1000 (optional decimation target).
- **`rig`**: Boolean flag to automatically attach a skeletal rig via UniRig after texturing.

---

## Diagnostics & Benchmarking Scripts

Specialized diagnostic routines live in [`scripts/`](scripts/):

```bash
# 1. Verify dual-GPU topology, PCIe bandwidth, and CUDA runtime environment
uv run --project engine python scripts/verify_gpus.py

# 2. Benchmark stage-by-stage VRAM usage under synthetic workload
# (Note: Unload LM Studio and Ollama models before measuring)
uv run --project engine python scripts/measure_stage_vram.py
```

To update client internationalization (extracting strings to `model3d_es.ts` and compiling `model3d_es.qm`):

```bash
client/scripts/update_translations.sh
```

---

## Testing & Quality Assurance

Each subproject maintains dedicated test suites executed via `pytest`:

```bash
# 1. Contracts models tests (17 passed)
(cd contracts && uv run pytest)

# 2. Client MVVM, views, and supervisor unit tests (111 passed)
(cd client && uv run pytest)

# 3. Blender add-on client and tracker tests (25 passed)
(cd blender && uv run pytest)

# 4. Engine API, GPU scheduler, and store tests (when vendor dependencies are installed)
(cd engine && uv run pytest)
```

### Pytest Custom Markers

- **`-m gpu`**: Tests requiring real dual RTX 5070 cards and resident weights (excluded by default in CI).
- **`-m webengine`**: Tests requiring QtWebEngine with hardware OpenGL rendering context.
- **`-m blender`**: Tests executing headless Blender CLI (`blender -b`).

---

## License

- The Blender add-on (`blender/model3d/`) is licensed under **GPL-3.0-or-later** as declared in [`blender/model3d/blender_manifest.toml`](blender/model3d/blender_manifest.toml).
- Third-party web libraries in `client/src/model3d_client/views/viewer/vendor/` follow their respective open source licenses as documented in `VENDOR.md`.
