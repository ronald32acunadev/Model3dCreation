# Rigging and Blender add-on (phase 4)

Objective: models with a skeleton ready to animate (new and existing), offered in the desktop app and a Blender 5.2 add-on. Branch: `feature/blender-addon`. Delivery: ask-on-risk. Test runners: `cd contracts|engine|client && uv run pytest -q` (markers `gpu`, `webengine`), `cd blender && uv run pytest -q` (marker `blender`).
Design: `docs/superpowers/specs/2026-10-22-rigging-and-blender-addon-design.md`. Plan: `docs/superpowers/plans/2026-10-22-rigging-and-blender-addon.md`.

## Specs

- S1: Skeleton on new jobs: "realmente lo que busque es generar modelos 3d con animaciones es decir tenga algun esqueleto ya hecho para implementarlo ya sea un video juego o una app". Only the skeleton: "Solo el esqueleto listo para animar; las animaciones las hago yo". Any object type: "Objetos de cualquier tipo". Model: UniRig ("UniRig alcanza, sigamos con el diseño").
- S2: Skeleton on existing library models: "Sí, también quiero poder agregarle esqueleto a modelos que ya están en la biblioteca". The original entry is kept (Claude's call).
- S3: Desktop app offers the skeleton option and "Add skeleton"; rigging unavailable is shown with its reason.
- S4: Blender add-on: pick an image, Generate, the textured model appears in the scene (owner: "Si"). Same options as the app ("SI por el momento asi"). If the engine is down it asks to open the desktop app ("Me lo pida"). It can open library models ("una opcion de poder abrirlo ahi").
- S5: Jobs without a skeleton keep their behavior; no out-of-memory risk from rigging.

## Tasks

| ID | Specs | Route | Status | Commit |
| :--- | :--- | :--- | :--- | :--- |
| T1 Contracts | S1, S2 | inline | committed | 89b8373 |
| T2 UniRig script and RigRunner | S1 | inline | committed | 672f1d0 |
| T3 Scheduler rig stage, card locks | S1, S5 | inline | committed | f7aafe0, d0a6fa5 |
| T4 API, health, wiring | S1, S2, S3 | inline | committed | d541cdc, 744424e |
| T5 GPU tests and setup guide | S1, S5 | inline | committed | 543e154 |
| T6 Client services and view model | S2, S3 | inline | committed | 30623e1 |
| T7 Client UI, rescan, Spanish | S3 | inline | committed | 3b0adea, 9cc88c8 |
| T8 Add-on core | S4 | inline | committed | 7d067d6 |
| T9 Add-on UI, packaging, docs | S4 | inline | committed | fedb3bc, 21cef77 |

Route evidence: one dependent chain (each task consumes the previous task's interfaces); no writer trigger fires, so the parent writes inline. Code authorship: LM Studio unreachable and Ollama without a loaded model on 2026-10-22, so the host model writes the code (`AGENTS.md` fallback).

## Log

- L1 (2026-10-22): "Dale arranquemos con la fase 4". Spike of UniRig succeeded (about 100 s and 5.5 GB per model). Owner approved the design ("Apruebo el diseño, pasá al plan") and the plan in native mode ("El plan está bien, ejecutalo en modo nativo").
- L2 (2026-10-22): "Continuemos con la fase 4". Every task is committed on the branch (table above); the task table had not been updated. Re-running the suites is blocked: `uv` and its managed Python (`~/.local/share/uv/python`) are missing from the machine, so every package `.venv` points at a deleted interpreter. Next: reinstall uv, `uv sync` each package, run the four unit suites, then push and PR (owner's decision).
- L3 (2026-10-22): Reinstalled uv 0.13.5 and `uv python install 3.11` (3.11.17); the existing `.venv`s work again without a sync. Unit suites with `uv run --no-sync pytest -q`: contracts 17 passed, engine 166 passed, client 111 passed, blender 25 passed. The CUDA toolkit (`/usr/local/cuda-13.1`) is also missing; it is only needed to rebuild extensions (`uv sync` in engine, UniRig's flash-attn), not to run tests. GPU and Blender suites not re-run.
- L4 (2026-10-22): Owner installed `cuda-toolkit-13-1` (nvcc V13.1.115). `scripts/verify_gpus.py`: RESULT: OK. `blender -m blender`: 2 passed. `engine -m gpu`: 7 passed in 27 min 43 s. Not re-run: `client -m gpu` and `client -m webengine`.
