# Desktop app (phase 3)

Objective: the PySide6 desktop app that starts the engine, turns an image into a 3D model, views it, saves it, and keeps a library. Branch: `feature/desktop-app`. Delivery: ask-on-risk. Test runner: `cd client && uv run pytest -q` (markers `webengine`, `gpu`); engine `cd engine && uv run --no-sync pytest -q`.
Design: `docs/superpowers/specs/2026-10-21-desktop-app-design.md`. Plan: `docs/superpowers/plans/2026-10-21-desktop-app.md`.

## Specs

- S1: Runtime data in a git-ignored `data/` at the repo root (`hf/`, `u2net/`, `jobs/`, `library/`); the weights move there with the owner's consent. "el modelo de IA local deberia estar en la misma ruta del proyecto".
- S2: Load an image and convert it to a 3D model: "Pienso cargar una imagen y convertirla a un modelo 3d". Live progress per stage, cancel ("Cancelando… y esperar").
- S3: Layout A: library left, Three.js viewer center, generation panel right; text-to-image is future only ("posiblemente a futuruo haya que generar imagenes tambien por texto").
- S4: Library kept until the user deletes entries (`data/library/`).
- S5: The app starts and stops the engine itself; diagnostics when something is missing; no warnings about third-party apps ("seria cosa del usuario").
- S6: "Por el momento Ingles y español , con una parte de configuracion" (language, generation defaults, export folder, advanced engine view).
- S7: Launcher and `.desktop` entry to open without a terminal.

## Tasks

| ID | Specs | Route | Status | Commit |
| :--- | :--- | :--- | :--- | :--- |
| T1 Engine data in `data/` | S1 | inline | done | 8baad13 |
| T2 Client project, paths, settings | S1, S6 | inline | done | 1038372 |
| T3 Library service | S4 | inline | done | 5b6e593 |
| T4 Engine API client | S2 | inline | done | 9a240a0 |
| T5 Engine supervisor | S5 | inline | done | 3d53c55 |
| T6 Diagnostics | S5 | inline | done | e7ef42f |
| T7 Generation view model | S2 | inline | done | 0c17fc9 |
| T8 Three.js viewer | S2, S3 | inline | done | ec2501e |
| T9 Main window | S2-S5 | inline | done | 180b49b, 2812972 |
| T10 Settings and Spanish | S6 | inline | done | 19b6888 |
| T11 Launcher, docs, acceptance | S7 | inline | done | 15aba4b |

Route evidence: one dependent chain (T7 consumes T3-T5, T9 consumes all); no writer trigger fires, so the parent writes inline. Code authorship: LM Studio and Ollama had no model loaded on 2026-10-21, so the host model writes the code (`AGENTS.md` fallback).

## Log

- L1 (2026-10-21): "Parece que es momento de continuar con la fase3". Owner approved the design ("Apruebo el diseño, pasá al plan") and the plan in native mode ("El plan está bien, ejecutalo en modo nativo").
- L2 (2026-10-22): T1-T11 landed; T4 also got 54a6cfb (keep API objects alive until requests finish). Added on the owner's request: the high detail quality level (6bda627, 31f108b, d2f597d, 57e5209). Branch merged to main through PR #1 (270fb87). Phase 3 closed; next is phase 4, the Blender add-on.
