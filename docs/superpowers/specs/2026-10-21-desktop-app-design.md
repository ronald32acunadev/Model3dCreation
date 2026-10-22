# Desktop app (phase 3) design

Date: 2026-10-21. Status: draft for the owner's approval.

## 1. Intent

The owner wants to load an image and turn it into a 3D model from a desktop app, without a terminal. They plan to share the app, so a person who knows nothing about the engine must be able to open it, see whether their machine can run it, generate a model, look at it, and save it.

The engine (phases 0-2 and the hardware-adaptive engine) is done: a FastAPI server on `127.0.0.1:8765` that turns one image into a GLB, streams progress over WebSocket, and picks a GPU run mode by itself. The API is in `docs/api.md`; shared request, event, and health models are in `contracts/` (`model3d_contracts`). This phase builds the client on top of it.

## 2. Goals

1. Load an image (drag and drop or file dialog), with a preview.
2. Generate a model with live progress per stage (shape, then paint), and cancel it.
3. View the result in an interactive 3D viewer: orbit, zoom, textured or untextured.
4. Save the GLB to a folder the user picks.
5. Keep a local library of past generations and reopen any of them in the viewer.
6. Start and stop the engine by itself; show its state and the run mode it chose.
7. Explain clearly what is missing when the machine or the setup cannot run the engine.
8. English and Spanish UI, chosen in a settings section.

## 3. Non-goals (this phase)

- Installing dependencies (CUDA toolkit, PyTorch environment, Hunyuan3D clone, weights). That is a separate project after this one (owner's decision). This phase only detects and reports what is missing.
- Sending models to Blender: phase 4 (the add-on). Here the GLB is saved to disk.
- Text-to-image or text-to-3D: a future feature. The generation panel must leave room for a second input source ("Text" next to "Image") without redesigning the window.
- Packaging with PyInstaller: phase 5.
- Windows and macOS: Linux only, like the engine.

## 4. Anti-goals

- The UI freezes while the engine works, or while the engine starts.
- The app hangs or shows a raw traceback when something is missing; it must say what is missing in plain words.
- The app names or warns about third-party programs the user may run (LM Studio, Ollama, and so on). What else runs on the user's GPUs is the user's business (owner's words). The app shows the run mode the engine chose and the free VRAM it found, nothing more.
- The app kills an engine it did not start.
- The viewer needs the internet: no CDN at runtime.

## 5. Decisions made with the owner

### 5.1 Window layout (option A)

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Model3D        ● Engine ready · dual_gpu · GPU0 shape, GPU1 paint  ⚙ │
├────────────┬──────────────────────────────────────┬──────────────────┤
│ Library    │                                      │ [Image] (Text*)  │
│ ┌────────┐ │                                      │ ┌──────────────┐ │
│ │penguin │ │            3D viewer                 │ │ drop image   │ │
│ │2:15 ✓  │ │   orbit · zoom · textured toggle     │ └──────────────┘ │
│ └────────┘ │                                      │ ☑ Texture        │
│ ┌────────┐ │                                      │ Quality: final ▾ │
│ │robot ✗ │ │                                      │ [Generate]       │
│ └────────┘ │                                      │ Shape ▓▓▓▓░░ 62% │
│            │                                      │ [Save GLB]       │
└────────────┴──────────────────────────────────────┴──────────────────┘
* "Text" is a future input source, not built in this phase.
```

- Top bar: engine state (starting, loading models, ready, error, stopped), the run mode name, and which GPU runs which stage, from `GET /v1/health`. A settings button opens the settings section.
- Left, library: one entry per generation with a thumbnail of the input image, a name (the image's file name), the date, the duration, and the outcome (succeeded, failed with the error message, cancelled). A click opens a succeeded entry in the viewer. Entries can be renamed and deleted.
- Center, viewer: Three.js in a `QWebEngineView`. Orbit and zoom with the mouse, a textured/untextured toggle, and a reset-camera button.
- Right, generation panel: input image with preview, texture on/off, quality (draft or final), Generate (Cancel while running), progress per stage with the engine's message, and Save GLB once a model is shown. After Cancel the panel shows "Cancelling…" until the engine confirms; the engine itself is not changed for this (owner's decision).
- Texture is disabled, with the reason shown, when the engine's mode cannot texture (`shape_only`, `mode.texture == false`).

### 5.2 Runtime data lives in the project folder

All runtime data goes in one git-ignored `data/` folder at the repository root, so the project folder is self-contained (owner's decision):

- `data/hf/`: model weights (`HF_HOME`). The 18 GB that are now in `~/.local/share/model3d/hf` move here.
- `data/jobs/`: the engine's job folders.
- `data/library/`: the app's library.

The engine's default `MODEL3D_HOME` changes from `~/.local/share/model3d` to `<repo>/data`; `MODEL3D_HOME` still overrides it. `docs/setup.md`, `docs/architecture.md`, `CLAUDE.md`, and the engine's config test change with it. Moving the existing weights is a one-time step done with the owner's consent during implementation (the target disk has 802 GB free).

This change lands first, before any client code (my call), so the client and its diagnostics only ever know `data/hf/`, and an engine started from a terminal finds the weights in the new place from then on.

### 5.3 Library

- Kept until the user deletes an entry; nothing is cleaned up automatically (owner's decision).
- Each entry stores the input image, the GLB (when it succeeded), the date, the options sent (texture, quality, seed), the engine's result stats (vertices, faces, duration), and the outcome.
- The library is the app's own: the engine has no endpoint to list jobs, and its job folders are working space, not an archive.

### 5.4 Engine lifecycle

- On start, the app checks `GET /v1/health` on `127.0.0.1:8765`. If an engine answers, the app uses it and never stops it. If none answers, the app starts one as a child process with `MODEL3D_HOME=<repo>/data` and owns it.
- While the engine detects GPUs and loads models (about 50 s), the top bar shows that, and Generate is disabled.
- On close, the app stops the engine only if it started it. If a job is running, it asks first ("A model is being generated. Close anyway?").
- If the engine the app owns exits unexpectedly, the top bar shows it and the diagnostics panel offers "Restart engine".

### 5.5 Diagnostics

When the engine cannot start or reports an error, a diagnostics panel lists each check with a plain-language result and what to do about it:

- An NVIDIA GPU and driver are present.
- The engine environment exists (`engine/.venv`).
- The Hunyuan3D clone exists (`vendor/Hunyuan3D-2.1`).
- The model weights exist in `data/hf/`.
- The engine started, and its health `error` (`mode_unavailable`, `worker_start_failed`, and so on) with its message.

This panel is where the future installer will be launched from.

### 5.6 Languages and settings

- UI in English and Spanish. The source strings are English; the Spanish text lives in a translation catalog, following the project's language rule. The first run picks the system language when it is Spanish, English otherwise. Changing the language applies at once, without a restart.
- Settings section:
  - Language: English or Español.
  - Generation defaults: texture on/off and quality, used to fill the panel.
  - Export folder: where Save GLB proposes to save.
  - Engine (advanced): run mode, GPUs with their stage and free VRAM, the engine log, and a "Restart engine" button.
- Left out on purpose: moving the `data/` folder from the app (easy to break the weights), and forcing the GPU mode (`MODEL3D_MODE`), which can come later.

## 6. Constraints

- Linux, NVIDIA GPUs, the engine from this repository. The app talks to the engine only through the documented HTTP and WebSocket API.
- The client is its own uv project in `client/` (package `model3d_client`), per `docs/architecture.md`. It depends on `contracts/` and never installs PyTorch.
- PySide6 with QtWebEngine; Three.js for the viewer, vendored in the repository so the viewer works offline.
- MVVM, per the phase 3 spec: views hold no logic, view models hold state and logic and are testable without a display, services wrap the engine API, the engine process, and the library.
- The UI thread never waits on the network or the engine process.
- The app sends no `Origin` header, so the engine's origin check does not apply to it (`docs/api.md`).
- Repository rules: English code and docs, Conventional Commits, source code synthesized by local LLMs first (`AGENTS.md`).

## 7. Calls I made (the owner did not decide these)

- Library entries are named after the input image's file name and can be renamed.
- Seed, background removal, and target faces stay at the engine's defaults; the panel does not expose them in this phase. The seed is stored in the library entry.
- The viewer has a reset-camera button and a textured/untextured toggle; no wireframe or lighting controls.
- Only one job runs from the app at a time: Generate is disabled while a job runs. The engine can queue more, but one at a time keeps the panel simple.
- A failed or cancelled generation still gets a library entry, so the user can see what happened and retry with the same image.
- Audience of this phase: a person with a clone of the repository whose environment was set up per `docs/setup.md` (until the installer exists). The app finds the repository root from its own location. To open it without a terminal, this phase ships a launcher script and a `.desktop` entry that runs `uv run --project client python -m model3d_client`; the user installs the entry by copying it to `~/.local/share/applications/`.

## 8. Left to the builder

- How the app reaches the engine (Qt's own network classes or a Python HTTP and WebSocket library), as long as the UI thread never blocks.
- How the GLB reaches the viewer (`QWebChannel`, a local file URL, or a local scheme handler).
- The library's storage format (a JSON index or SQLite) and folder layout under `data/library/`.
- How the app finds the repository root and starts the engine (for example `uv run --project engine python -m model3d_engine`), and where the engine's log goes.
- The translation mechanism (Qt Linguist `.ts`/`.qm` files or another catalog).
- The test split: view models and services unit-tested without a display; a small number of tests that need a display or the real engine, marked like the engine's `gpu` tests.

## 9. Acceptance

- With the engine stopped, opening the app starts it, shows the loading state, and enables Generate once health reports ready.
- With an engine already running, the app uses it, and closing the app leaves it running.
- Generating from an image shows shape and paint progress, then the textured model in the viewer; the window stays responsive throughout.
- Cancel shows "Cancelling…" at once, and the library records the job as cancelled when the engine confirms it. The engine cancels a running job at its next stage boundary, so this can take up to about 75 s during paint; the owner accepted that wait over changing the engine (2026-10-21).
- Save GLB writes a file that opens in Blender 5.2 with its textures.
- Restarting the app shows the same library, and every succeeded entry opens in the viewer.
- With the weights folder missing, the diagnostics panel says the weights are missing instead of the app hanging.
- Switching to Español changes every visible string at once.
