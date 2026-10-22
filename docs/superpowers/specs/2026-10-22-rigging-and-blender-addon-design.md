# Phase 4: automatic rigging and the Blender add-on

Date: 2026-10-22. Status: draft for the owner's review. Branch: `feature/blender-addon`.

## Intent

The owner wants to turn an image into a 3D model that is ready to animate, and to use it in a video game or an app. In their words: "realmente lo que busque es generar modelos 3d con animaciones es decir tenga algun esqueleto ya hecho para implementarlo ya sea un video juego o una app". The skeleton is the product; the owner animates by hand: "Solo el esqueleto listo para animar; las animaciones las hago yo".

They also want to work from Blender: pick an image in a panel, press Generate, and get the model in the scene with its texture, ready to move or edit.

`Fases/fase-4-addon-blender-integration.md` only says "a native Blender add-on that generates models with the local backend and imports them into the scene"; everything else in it was stripped. This document replaces it.

## Goals

1. **Rigging in the engine.** A job can ask for a skeleton. The engine then runs UniRig after shape (and paint), and the result GLB carries an armature bound to the mesh with skinning weights, any object type, textures kept.
2. **Rigging existing models.** A model already in the library can get a skeleton without regenerating it: the engine accepts a rigging-only job whose input is a GLB and runs only UniRig (about 100 s instead of 4-5 minutes).
3. **Rigging in the desktop app.** The generation panel offers the same option, the library offers "Add skeleton" on an existing entry, and the viewer shows the rigged result.
4. **The Blender add-on.** A panel in Blender 5.2 with the same options as the desktop app (image, quality including high detail, texture, skeleton) that submits a job to the running engine, shows progress, and imports the result into the scene. It can also open models that are already in the library.

Order of work: rigging in the engine first, then the desktop app option, then the add-on. If rigging had to be dropped, the add-on still stands on its own.

## Non-goals

- Animations of any kind (walk cycles, retargeting, video-guided motion). The owner animates.
- Text-to-image or text-to-3D.
- SkinTokens or any other rigging model. UniRig was chosen after the spike ("UniRig alcanza").
- Fixing UniRig's skeletons (adding tail or wing bones). The owner adds those by hand when needed.
- Starting the engine from Blender.

## Anti-goals (a failure even if it works)

- Blender's interface freezes while a job runs.
- Jobs without a skeleton get slower, use more VRAM, or change their output.
- The add-on starts or stops the engine on its own. The owner wants to be asked to open the desktop app instead ("Me lo pida").
- An out-of-memory crash because rigging was squeezed next to a resident worker.

## Decisions the owner made

- The add-on uses the engine on `127.0.0.1:8765`. If the engine does not answer, the add-on tells the user to open the Model3D desktop app; it never starts it.
- The add-on offers the desktop app's generation options, and nothing more for now ("SI por el momento asi").
- The add-on can open models from the library in Blender ("una opcion de poder abrirlo ahi").
- Rigging covers objects of any type, inferred by the model from the mesh ("Objetos de cualquier tipo").
- Rigging uses UniRig; only the skeleton and skinning weights, no animations.
- Order: spike first (done), then rigging in the engine, then the add-on (playback approved with "perfecto").
- Existing library models can get a skeleton too: "Sí, también quiero poder agregarle esqueleto a modelos que ya están en la biblioteca".

## What the spike established (2026-10-22)

Throwaway spike in `vendor/UniRig` (git-ignored clone of `VAST-AI-Research/UniRig` at `6793c66`), run on three textured models from `data/library/`: a dog character, a cat character, and an armored hero.

- **License.** Code MIT; the weights on Hugging Face (`VAST-AI/UniRig`) are MIT too.
- **Weights.** `skeleton/articulation-xl_quantization_256/model.ckpt` (1.4 GB) and `skin/articulation-xl/model.ckpt` (4.4 GB), downloaded into `data/hf`.
- **Environment.** UniRig needs its own Python 3.11 environment, separate from the engine's: `torch==2.9.1`/`torchvision==0.24.1` from the cu130 index, UniRig's `requirements.txt` (which pins `bpy==4.2` and `transformers==4.51.3`), `spconv-cu126` 2.3.8 (its kernels run on sm_120), `torch_scatter` and `torch_cluster` from the PyG `torch-2.9.1+cu130` wheels, `numpy==1.26.4`, and `flash-attn` 2.8.3 built from source with `FLASH_ATTN_CUDA_ARCHS=120` (about 18 minutes, needs `CUDA_HOME=/usr/local/cuda-13.1`, `ninja`, `packaging`, `psutil`, `setuptools`). `flash_attn` is imported at startup, so even the skeleton step needs it.
- **One incompatibility.** PyTorch 2.6+ loads checkpoints with `weights_only=True`, and the skin checkpoint pickles a `box.Box`. The spike allowlisted that one class with `torch.serialization.add_safe_globals([Box])` in `run.py`; `weights_only=False` is not acceptable.
- **Pipeline.** UniRig's own scripts: `launch/inference/generate_skeleton.sh` (mesh to skeleton FBX), `generate_skin.sh` (skeleton to skinned FBX), `merge.sh` (skin onto the original GLB, output GLB).
- **Cost per model.** About 100 s: skeleton 33-40 s, skin 40-51 s, merge 16-20 s. Peak 5.5 GB of VRAM on one RTX 5070 (nvidia-smi, includes the CUDA context).
- **Quality.** Textures and materials survive the merge, every vertex is weighted, and posing the head, an arm, and a knee in Blender deforms the mesh cleanly.
- **Limits.** All three got humanoid skeletons (22 bones; the cat 28, with hand bones). Neither animal got tail bones; the tail follows the hips. The seed does not change the skeleton. Bones are named `bone_0` to `bone_N`.

## Constraints

- **VRAM.** Measured per worker (architecture section 2): shape holds 7.2 GiB and peaks at 9.0 GiB; paint holds 4.9 GiB and peaks at 10.2 GiB. Rigging peaks at 5.5 GB. Rigging does not fit next to a resident shape worker on a 12 GB card, and does not fit while paint runs; it may fit next to an idle paint worker (4.9 + 5.5 GiB against about 11.7 GiB free on GPU0 at idle), but that has not been measured. Whatever placement the builder picks must be measured and must not risk out-of-memory, in every run mode (`dual_gpu`, `single_gpu_resident`, `single_gpu_swap`).
- **Separate process and environment.** UniRig's dependencies (`bpy` 4.2, `transformers` 4.51.3, `flash_attn`, `spconv`) do not belong in the engine's environment. Rigging runs as its own process with `vendor/UniRig/.venv`, the same way the engine already isolates GPU work in spawned workers. The API process still never imports torch.
- **Optional install.** The UniRig clone, its environment, and its weights are an optional install, documented in `docs/setup.md` like Hunyuan3D. When they are missing, the engine still runs, health reports that rigging is unavailable, and a job asking for a skeleton is rejected with a clear error. The desktop app's diagnostics explain what is missing.
- **Blender 5.2.2 LTS** is installed (snap). It bundles Python 3.13.13 (the phase document's "Python 3.11" is wrong for 5.x). The add-on uses only the standard library, since it cannot install packages into Blender's Python.
- **Blender's main thread.** Network calls must not block the interface; the add-on polls from a timer (`bpy.app.timers`) or a background thread that hands results back to the main thread. Scene changes happen on the main thread only.
- **Existing API.** `POST /v1/jobs` with an image and JSON options, `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/result` (GLB), `DELETE /v1/jobs/{id}` (cancel), `GET /v1/health`, and the events WebSocket (`docs/api.md`). Blender's standard library has no WebSocket client, so polling `GET /v1/jobs/{id}` is the expected way to follow progress.
- **English by default** in code, identifiers, and docs (`AGENTS.md`); user-facing text goes through translation catalogs. The desktop app is English and Spanish.

## How it should behave

### A job with a skeleton

1. The client sends `"rig": true` in the job options (default `false`).
2. The engine runs shape, then paint if `texture` is true, then rigging, and stores one GLB with the armature and the skinned mesh.
3. Progress reports rigging as its own stage, so the desktop app and the add-on can show it.
4. If rigging fails, the job fails with a clear error code; the job does not silently return an unrigged model.

### Adding a skeleton to an existing model

1. The client submits a rigging-only job with the model's GLB as input (the API shape is the builder's; it must stay distinct from image jobs so image validation and options do not leak into it).
2. The engine runs only rigging, with the same mesh-size rule, and stores the rigged GLB as the job result.
3. The client saves the result as a **new library entry** next to the original, which stays untouched. Its name marks it as rigged, and its entry records which entry it came from.

### The desktop app

- The generation panel gains a "With skeleton" option next to texture. It is disabled, with the reason, when health reports rigging is unavailable.
- Each library entry without a skeleton offers "Add skeleton", with the same availability rule.
- The library keeps rigged models like any other, and the viewer shows them (Three.js reads skinned GLBs).

### The Blender add-on

- A sidebar panel in the 3D viewport: choose an image, quality, texture, skeleton; Generate; progress with the current stage; Cancel.
- Before submitting it checks health. Engine not answering: "Open the Model3D desktop app" (no auto-start). Engine still loading: says so and waits.
- On success it imports the GLB into the current scene (the armature with the mesh parented to it when rigged) and selects it.
- A way to browse the library and import an existing model into the scene, and to add a skeleton to one (same flow as the desktop app, result imported into the scene).
- Models generated from Blender are also saved to the library, so they show up in the desktop app.

## Calls made by Claude (the owner can override)

- **Rigging is opt-in per job**, so jobs without a skeleton are untouched.
- **Rigging an existing model creates a new entry** and never overwrites the original; changing stored models is hard to undo.
- **Rigging lives in the engine**, not in the add-on, so the desktop app and any future client get it too.
- **Library writes from Blender use the desktop app's library format** (`data/library/<entry_id>/entry.json`, `input.<ext>`, `model.glb`). The add-on cannot import the client package (different Python), so the builder must keep the two writers compatible, or move library writes behind one owner.
- **The add-on finds the repository** through an add-on preference holding the Model3D folder (for `data/library/`).
- **Bone names**: generic `bone_N` names are kept in phase 4. Renaming bones by role (head, arm, leg) would only be right for humanoids, and objects of any type are the goal.
- **Add-on language**: English, with Spanish through Blender's translation system, matching the desktop app.
- **Mesh size for rigging.** Untextured jobs are not decimated today (one library model has 862,462 faces), while the spike measured textured models capped at 150,000 faces. A job with `rig: true` decimates to the same 150,000-face default (or `target_faces` when given) before rigging, so the measured cost holds. Untextured rigged jobs are new, so this changes no existing output.
- **Closing the desktop app during a Blender job.** The app stops the engine it owns, and the job dies with it. That is accepted: the add-on reports that the engine went away and asks the user to open the app again. The supervisor and the API do not change for this.
- **New library entries from Blender** appear in an open desktop app without a restart: the app rescans `data/library/` when its window regains focus (or on a cheap timer).
- **The `Box` allowlist** must survive a fresh clone of `vendor/UniRig`, so it lives in the engine's wrapper (or a documented patch step), not only as a hand edit in the clone.

## Left to the builder

- Where rigging runs on each run mode, and whether it swaps with a worker; measured, with the numbers recorded in `docs/architecture.md`.
- Whether to call UniRig's shell scripts or its Python entry points directly, and how the intermediate FBX files are cleaned up.
- How rigging availability appears in `GET /v1/health` and the diagnostics.
- The add-on's packaging (Blender extension with `blender_manifest.toml` versus a legacy add-on) and how the owner installs it.
- Tests: the engine and client test suites keep their markers (`gpu` for real GPU runs); the add-on needs a way to test its non-Blender logic outside Blender, plus a headless Blender check (`blender -b`) for import and panel registration.

## Success looks like

From Blender, the owner picks an image, ticks "With skeleton", presses Generate, and after a few minutes a textured model with an armature appears in the scene. They can pose it right away, and export it to a game engine.
