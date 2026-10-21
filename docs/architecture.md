# Architecture and Stack (Phase 0)

Local AI 3D-generation engine: an image goes in, a textured GLB comes out. Everything runs on one workstation, with no cloud calls. This document completes `Fases/fase-0-arquitectura-y-stack.md` and corrects the parts of the phase specs that do not match the hardware.

## 1. Overview

Decoupled engine and clients. The engine owns the GPUs and is the only process that imports PyTorch; clients talk to it over HTTP and WebSocket on `127.0.0.1`.

```text
 PySide6 desktop app ──┐                     ┌── Shape worker ── GPU0 (25:00.0, x2)
 (Three.js viewer)     │  REST + WebSocket   │   Hunyuan3D-Shape, weights resident
                       ├──────────────────► Engine (FastAPI, uvicorn)
 Blender add-on ───────┘  127.0.0.1:8765     │   job queue, job store, events
                                             └── Paint worker ── GPU1 (29:00.0, x16)
                                                 Hunyuan3D-Paint diffusion + VAE
                                                 (DINOv2 encoder on the CPU)
```

- A job runs as a two-stage pipeline: shape, then paint. The two stages live on different GPUs, so job N can paint while job N+1 builds its shape.
- The UI never blocks on inference: clients submit a job, then follow its events.
- Untextured output (shape only) is a supported job option; it skips the paint stage.

## 2. Hardware mapping (verified on this machine)

| Component | Measured | Role |
| :--- | :--- | :--- |
| CPU | Ryzen 7 5800X, 8C/16T | API server, mesh post-processing, UV unwrapping, paint's DINOv2 encoder |
| RAM | 76 GiB | Checkpoint cache, mesh processing |
| GPU0 | RTX 5070 12 GB, bus `25:00.0`, link PCIe 2.0 x2 | Shape stage only |
| GPU1 | RTX 5070 12 GB, bus `29:00.0`, link PCIe 3.0 x16 | Paint diffusion; also drives the desktop (about 1.6 GB) |
| Both GPUs | compute capability 12.0 (sm_120), driver 595.99.02 | |
| OS | Ubuntu 26.04.1 LTS, kernel 7.0 | |

### Stage placement (measured in phases 1 and 2)

The phase 2 spec puts shape on the fast x16 card and paint on the x2 card. This design inverts it, and the spike confirmed it works without CPU offload:

| Work | GPU | Time (demo image, 6 views at 512) | Peak VRAM |
| :--- | :--- | :--- | :--- |
| Shape (Hunyuan3D-Shape), resident | GPU0, x2 | 77 s per job | 7.6 GiB allocated, 8.7 GiB held by the worker |
| Paint diffusion and VAE (decode one view at a time) | GPU1, x16 | 58 s per job | 8.7 GiB in the phase 1 spike |
| Paint DINOv2 image encoder (float32) | CPU | inside the 58 s (about 3 s) | none |

Both workers load in about 51 s when the engine starts (phase 2 end-to-end test).

Why this split:

- The two cards hold 24 GB together, but that memory is not pooled: the RTX 5070 has no NVLink, so each tensor lives on one card and anything shared crosses PCIe. Upstream quotes about 10 GB for shape and 21 GB for paint, so paint cannot sit on one card.
- GPU1 also drives the desktop, which takes about 1.6 GB of it. With all of paint on GPU1, the job runs out of memory, first during diffusion (with DINOv2 there) and then during the VAE decode of 12 images (6 views, albedo and metallic-roughness).
- The phase 1 spike moved DINOv2 and the VAE decode to GPU0. With the shape worker resident there, that ran out of memory: the shape worker holds about 8.7 GiB of GPU0 (weights plus PyTorch's cached blocks), so the 2.7 GiB of helpers did not fit.
- DINOv2 runs once per job and returns a small embedding, so it moved to the CPU (float32): about 3 s per job, and GPU0 stays free for shape. The VAE decode stayed on GPU1 but decodes one view at a time (`enable_slicing`); the phase 1 overflow came from decoding all 12 views as one batch.
- Shape fits on GPU0 and its weights stay resident: per job only an image and a mesh cross the slow link. Job N can paint on GPU1 while job N+1 builds its shape on GPU0.

Placement is resolved by PCI bus ID, never by CUDA index, since CUDA ordering can change. Defaults: shape on `0000:25:00.0`, paint on `0000:29:00.0`, paint helpers on the CPU, overridable by environment variables. Set `CUDA_DEVICE_ORDER=PCI_BUS_ID` in the engine so CUDA indexes match `nvidia-smi`; `scripts/verify_gpus.py` prints the placement.

Upstream's paint code has a `multiple_devices` path (albedo attention on `cuda:0`, metallic-roughness on `cuda:1`), but it is never enabled and only moves some attention layers. It is not used.

### Runtime rules for the paint stage

- Import `bpy` before anything imports `pymeshlab`: both ship `libembree4.so.4`, and loading pymeshlab's older copy first makes `bpy` fail with `undefined symbol: rtcGetSceneTraversable`.
- Call upstream's `torchvision_fix.apply_fix()` before importing the paint pipeline (`basicsr` imports a module removed from torchvision).
- Apply `engine/patches/hunyuan3d-paint-device.patch`: upstream hardcodes `.to("cuda:0")` in `attn_processor.py`, so paint only runs on CUDA device 0. The Hugging Face model snapshot ships its own copy of that file (`hunyuan3d-paintpbr-v2-1/unet/attn_processor.py`), which is the one diffusers loads, so the patch applies to both (see [setup.md](setup.md)).
- After an exception, the process can hang at interpreter teardown in a native thread; workers exit with `os._exit`.

## 3. Stack (corrected versions)

| Layer | Choice | Correction from the phase specs |
| :--- | :--- | :--- |
| Generation models | Hunyuan3D 2.1 (shape + PBR paint), vendored clone, weights in a local Hugging Face cache | TRELLIS and TripoSR stay out of v1: one model family keeps the pipeline and the VRAM budget simple |
| Engine Python | 3.11, managed by uv | Hunyuan3D 2.1 is tested on 3.10, but its paint pipeline imports `bpy`, whose wheels exist for cp311 only (4.2 to 5.0); `bpy==4.0` for 3.10 is gone from PyPI |
| PyTorch | 2.9.1+cu130, the oldest cu130 build (closest to upstream's tested 2.5.1) | Spec says 2.5 + CUDA 12.4, which has no sm_120 kernels |
| CUDA toolkit (nvcc, for the paint extensions) | `cuda-toolkit-13-1` from Ubuntu multiverse | Ubuntu's `nvidia-cuda-toolkit` is 12.4, too old for sm_120; major version must match PyTorch's CUDA 13 |
| API | FastAPI + uvicorn, Pydantic v2 | |
| Desktop client | PySide6 + QtWebEngine, Three.js viewer | |
| Blender add-on | Blender 5.2 (embedded Python 3.13), standard library HTTP only | Spec says Python 3.11 embedded; Blender 5.2.2 ships 3.13.13 |
| Packaging | PyInstaller (client only) | |

The engine and the desktop client are separate Python environments: the client never installs PyTorch, and it can use a newer Python than the engine.

The paint pipeline imports `bpy` (`DifferentiableRenderer/mesh_utils.py`) to convert OBJ to GLB, so the engine installs `bpy==4.5.*` (cp311). It also needs upstream's `torchvision_fix.apply_fix()` before import, because `basicsr` imports a module removed from torchvision.

## 4. Repository structure

```text
.
├── AGENTS.md, CLAUDE.md, README.md, .agents/   # agent rules (existing)
├── docs/
│   ├── architecture.md                         # this document
│   └── api.md                                  # contract reference (phase 2)
├── contracts/                                  # shared Pydantic models, no heavy deps
│   ├── pyproject.toml                          # package model3d_contracts, Python >=3.10
│   └── src/model3d_contracts/{jobs.py, events.py}
├── engine/                                     # GPU backend
│   ├── pyproject.toml                          # package model3d_engine, Python 3.10, depends on contracts
│   ├── src/model3d_engine/
│   │   ├── api/                                # FastAPI routers (REST + WebSocket)
│   │   ├── jobs/                               # queue, job store, state machine
│   │   ├── gpu/                                # topology discovery, placement by bus ID
│   │   ├── stages/                             # shape and paint adapters over Hunyuan3D
│   │   └── config.py                           # settings from environment
│   └── tests/
├── client/                                     # PySide6 app (phase 3)
│   ├── pyproject.toml                          # package model3d_client, depends on contracts
│   ├── src/model3d_client/{views, viewmodels, services, viewer/}
│   └── tests/
├── blender_addon/model3d_bridge/               # Blender extension (phase 4)
├── scripts/verify_gpus.py                      # phase 1 environment check
└── vendor/                                     # git-ignored: Hunyuan3D-2.1 clone
```

Runtime data lives outside the repository tree by default, in `~/.local/share/model3d/` (Hugging Face cache in `hf/`, job folders), overridable with `MODEL3D_HOME`. Environment setup: [setup.md](setup.md).

## 5. API contracts (v1)

Base URL `http://127.0.0.1:8765/v1`. Bound to loopback only, so there is no authentication in v1. All timestamps are ISO 8601 UTC.

### Job states

`queued → shaping → painting → succeeded`, with `failed` and `cancelled` reachable from any non-final state. Shape-only jobs go `shaping → succeeded`.

### `POST /v1/jobs`

`multipart/form-data` with two parts: `image` (PNG or JPEG) and `options` (JSON).

```json
{
  "texture": true,
  "quality": "final",
  "seed": 1234,
  "remove_background": true,
  "target_faces": 40000
}
```

| Field | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `texture` | bool | `true` | `false` skips the paint stage |
| `quality` | `"draft"` or `"final"` | `"final"` | Maps to octree resolution and step count |
| `seed` | int or null | `null` | `null` picks a random seed, returned in the job |
| `remove_background` | bool | `true` | |
| `target_faces` | int or null | `null` | Decimation target; `null` keeps the model output |

Response `202 Accepted`:

```json
{
  "job_id": "01JBQ7Z8K3M5N6P7Q8R9S0T1V2",
  "state": "queued",
  "queue_position": 0,
  "seed": 1234,
  "created_at": "2026-10-20T19:05:00Z",
  "links": {
    "self": "/v1/jobs/01JBQ7Z8K3M5N6P7Q8R9S0T1V2",
    "events": "/v1/jobs/01JBQ7Z8K3M5N6P7Q8R9S0T1V2/events",
    "result": "/v1/jobs/01JBQ7Z8K3M5N6P7Q8R9S0T1V2/result"
  }
}
```

Errors use one shape, with a stable `code`: `400 invalid_image`, `400 invalid_options`, `413 image_too_large`, `503 engine_not_ready`.

```json
{ "error": { "code": "invalid_options", "message": "target_faces must be at least 1000" } }
```

### Other endpoints

| Method and path | Returns |
| :--- | :--- |
| `GET /v1/jobs/{job_id}` | The job (same shape as the `202` body, plus `progress`, `stage`, `error`, `finished_at`) |
| `GET /v1/jobs/{job_id}/result` | `model/gltf-binary` once `succeeded`; `409 job_not_finished` otherwise |
| `DELETE /v1/jobs/{job_id}` | `202` and the job moves to `cancelled` at the next stage boundary |
| `GET /v1/health` | Engine readiness, and per GPU: bus ID, stage, model loaded, VRAM used and total |

### WebSocket `GET /v1/jobs/{job_id}/events`

Server to client only. On connect the server sends a `snapshot` event with the current state, so a client that connects late misses nothing. The server closes the socket after a final event (`completed`, `failed`, `cancelled`).

```json
{ "type": "snapshot", "job_id": "01JB...", "state": "shaping", "stage": "shape", "progress": 0.35, "ts": "2026-10-20T19:05:12Z" }
```

```json
{ "type": "progress", "job_id": "01JB...", "stage": "paint", "progress": 0.62, "message": "Baking view 4 of 6", "ts": "2026-10-20T19:05:41Z" }
```

```json
{
  "type": "completed",
  "job_id": "01JB...",
  "result_url": "/v1/jobs/01JB.../result",
  "stats": { "vertices": 21034, "faces": 40000, "textured": true, "duration_s": 74.2 },
  "ts": "2026-10-20T19:06:14Z"
}
```

```json
{ "type": "failed", "job_id": "01JB...", "error": { "code": "gpu_out_of_memory", "message": "Paint stage ran out of VRAM on 00000000:29:00.0" }, "ts": "2026-10-20T19:05:50Z" }
```

`progress` is 0.0 to 1.0 within the current stage.

## 6. Acceptance criteria carried from phase 5

| Criterion | Target | Note |
| :--- | :--- | :--- |
| VRAM | Under 11.0 GB per GPU | Measured: GPU0 8.7 GiB (shape worker), GPU1 8.7 GiB plus the desktop |
| Time per job | About 2 min 15 s (77 s shape + 58 s paint) with warm models | The spec's TripoSR/TRELLIS figures do not apply to Hunyuan3D 2.1 |
| Client UI | Stays responsive while the engine works | |
| GLB | Loads in Blender 5.2 with PBR textures attached | |

## 7. Decisions made in this document

These are the author's calls, not the owner's, and are open to change:

1. Shape on the x2 card, paint on the x16 card, DINOv2 on the CPU, no weight offload (section 2, measured).
2. Hunyuan3D 2.1 only in v1; TRELLIS and TripoSR are out.
3. Python 3.11 + PyTorch 2.9.1+cu130 + CUDA 13.1 toolkit.
4. A shared `contracts` package instead of duplicating schemas in each client.
5. Port 8765, loopback only, no authentication.
