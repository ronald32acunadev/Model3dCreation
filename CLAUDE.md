# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## What this repository is

Model3D: a local, GPU-only pipeline that turns an image into a textured (optionally rigged) GLB with Hunyuan3D 2.1 and UniRig, plus two clients for it. Target hardware is two RTX 5070 (12 GB) cards on Linux x86_64. The phase specs live in `Fases/` (Spanish).

Five independent `uv` projects, each with its own `pyproject.toml`, lockfile and `.venv`:

| Project | Role |
|---|---|
| `contracts/` | Pydantic models (`jobs`, `events`, `health`) shared by engine and desktop client. Editable path dependency of both. |
| `engine/` | FastAPI server (`python -m model3d_engine`, default `127.0.0.1:8765`). Python 3.11 only, CUDA 13 torch, `bpy` wheel. |
| `client/` | PySide6 desktop app (`python -m model3d_client`), including the first-run setup wizard. |
| `blender/` | Blender add-on `blender/model3d/` (stdlib `urllib` only, no contracts dependency); the uv project only runs its tests outside Blender. |
| `packaging/` | Builds the `.deb` (`packaging/deb/build.sh`); the uv project only runs its tests. |

`vendor/Hunyuan3D-2.1`, `vendor/UniRig`, and the optional `vendor/TRELLIS.2` (each of the last two with its own `.venv`) are not tracked; the engine path-depends on `vendor/Hunyuan3D-2.1/hy3dpaint/custom_rasterizer`, so `uv sync` in `engine/` needs that clone and `nvcc` (`CUDA_HOME`). `engine/patches/hunyuan3d-paint-device.patch` must be applied to the Hunyuan clone (it removes hardcoded `cuda:0`).

## Commands

Run each from its project directory (or use `uv run --project <dir>`):

```bash
uv sync                                   # per project
uv run pytest                             # default: GPU/Blender/WebEngine tests excluded via addopts
uv run pytest tests/test_scheduler.py::test_name   # single test
uv run pytest -m gpu                      # engine/client: needs both GPUs + full engine env
uv run pytest -m webengine                # client: needs a real display with GPU rendering
uv run pytest -m blender                  # blender: needs `blender` on PATH
uv run python -m model3d_engine           # start the engine (from engine/)
uv run python -m model3d_client           # start the app (from client/); client/launch.sh does the same from anywhere
client/scripts/update_translations.sh     # refresh model3d_es.ts and recompile the committed .qm
client/scripts/test_system_python.sh      # client tests on apt's PySide6/pydantic, as the .deb runs
packaging/deb/build.sh [--out DIR]        # dist/model3d_<version>_amd64.deb; version comes from client/pyproject.toml
uv run --project engine python scripts/verify_gpus.py
uv run --project engine python scripts/measure_stage_vram.py   # unload LM Studio/Ollama models first
```

No linter or formatter is configured.

## Engine architecture

`__main__.build_engine` wires everything:

1. **GPU placement** (`gpu/probe.py`, `gpu/modes.py`): detected GPUs pick a run mode — `dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, `shape_only`, or `unsupported`. `unsupported` still starts the API; jobs then fail with the mode's error. Rigging runs on the paint card (shape card in `shape_only`).
2. **Stage workers** (`stages/process.py`): each stage (`shape`, `paint`) is a `ProcessStageRunner` — a separate process created from a `module:factory` string (`stages/hunyuan.py:make_shape_stage` / `make_paint_stage`) that holds its model on one device. The engine starts with no model loaded: `Scheduler` loads a stage's worker for its task and unloads it when no task waits for it. A worker is killed on timeout, and after a crash the next task loads it again. Tests inject fake factories through those strings. `stages/rig.py` runs UniRig as a subprocess in UniRig's own venv, and `stages/smooth_skin.py` then smooths its stepped skin weights. With `MODEL3D_GENERATOR=trellis2`, the shape slot hosts `stages/trellis.py`, a proxy that drives a persistent `stages/trellis_worker.py` process in `vendor/TRELLIS.2/.venv` over JSON lines (the worker must not import engine code); it returns a textured result, so the scheduler skips paint (`NoStage`), and it runs on one card chosen by `gpu/modes.py:choose_generator_mode`.
3. **Scheduler** (`jobs/scheduler.py`): pipelines jobs shape → paint → optional rig, one task per card at a time, parking/reloading workers in swap modes. Cancel is immediate when queued, otherwise at the next stage boundary. `JobStore` persists records under `$MODEL3D_HOME/jobs` (default `data/`); `EventBus` publishes progress/completed/failed/cancelled events.
4. **API** (`api/app.py`): `/v1/health`, `POST /v1/jobs` (image), `POST /v1/rig-jobs` (GLB to rig only), `GET|DELETE /v1/jobs/{id}`, `/v1/jobs/{id}/result`, WebSocket `/v1/jobs/{id}/events`. Request errors answer `{"error": {"code", "message"}}`; a stage's `StageError(code, message)` becomes the failed job's `JobError`.

Configuration is env-only (`config.load_settings`): `MODEL3D_HOME`, `MODEL3D_HUNYUAN_DIR`, `MODEL3D_UNIRIG_DIR`, `MODEL3D_HOST`, `MODEL3D_PORT`, `MODEL3D_MODE`, `MODEL3D_SHAPE_BUS` + `MODEL3D_PAINT_BUS` (both or neither, PCI bus ids), `MODEL3D_ALLOWED_ORIGINS`, `MODEL3D_STAGE_TIMEOUT_S`, `MODEL3D_GENERATOR` (`hunyuan` default or `trellis2`), `MODEL3D_TRELLIS_DIR`.

`engine/pyproject.toml` pins are deliberate and commented (numpy for `bpy`, trimesh 4.4.7 positional decimation, onnxruntime, setuptools<81). Read the comment before bumping any of them.

## Client architecture

MVVM with Qt: `services/` (no widgets) → `viewmodels/` (state + signals) → `views/` (widgets only). `EngineApi` is fully async on `QNetworkAccessManager`; callbacks run on the UI thread and the instance must outlive its pending replies. `EngineSupervisor` starts `<repo>/engine/.venv/bin/python -m model3d_engine` only when no engine answers, and stops it only if it started it. The 3D viewer is a QtWebEngine page (`viewer/index.html`, `viewer.js`, vendored libs in `viewer/vendor/` per `VENDOR.md`). Tests run a fake FastAPI engine (`client/tests/conftest.py`); UI strings go through Qt `tr()` and the Spanish catalog in `i18n/`.

**Install location.** `paths.app_paths` resolves the "repo" root: the install folder named by the pointer `$XDG_CONFIG_HOME/model3d/config.json` (fallback `~/.config/model3d/`) when present, otherwise this checkout. `MODEL3D_HOME` moves the data folder for both app and engine. Path/state helpers read an env mapping passed in, so tests never touch the real pointer.

**Setup wizard** (`setup/`): `checks.py` probes driver, GPUs, VRAM, CUDA 13 `nvcc`, compilers and the target folder; `recipe.py` declares every install step as data (Python 3.11, engine, Hunyuan3D clone + patch + native builds, UniRig, HF weights, a final engine-mode check that fails when the GPUs are busy); `runner.py` executes them, recording each finished step with a fingerprint in the folder's `install.json` so it can cancel, resume, and repair. The app decides its first window (wizard vs. main) from that install state. Tests must not run real checks or installs.

## Blender add-on

`blender/model3d/` is an extension (`blender_manifest.toml`). `engine_client.py` talks to the same HTTP API with `urllib`, mapping errors to `EngineError` / `EngineUnreachable`; `jobs.py`/`tracker.py` poll jobs, `library.py` handles saved models (found through the desktop app's install pointer), `ui.py` holds panels and operators. Unit tests use `tests/fake_engine.py`; `test_blender_smoke.py` runs `blender -b --factory-startup --python tests/blender_smoke.py`.

## Packaging

The `.deb` installs the client and contracts sources under `/opt/model3d/lib`, the engine/contracts sources and patches for the wizard to build from, a pinned `uv`, the Blender add-on, and a `/usr/bin/model3d` launcher. At runtime the client uses the system Python with apt's PySide6 and pydantic, which is why `test_system_python.sh` exists. Heavy pieces (engine venv, vendor clones, weights) are installed by the wizard, not the package.

## Code delegation in Claude Code

The delegation rule in `AGENTS.md` covers source code, tests, and scripts; docs, config, and commit messages are written by the agent. In Claude Code the lightweight implementer is a subagent (Agent tool) on a smaller model such as `haiku`. Do not use the local LLM MCP servers (`pumbastudio`, `pumbalama`) for code: the GPUs are reserved for the engine. Prompt rules for the implementer live in `.agents/local-llm-prompt-rules.md`.
