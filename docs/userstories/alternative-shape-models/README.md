# Alternative 3D models

Status: US-01 and US-02 complete; US-03 in progress.

## Context

On 2026-10-29 the user asked to start evaluating several 3D models in one pipeline, each handling a different step and freeing memory between steps. The face-detail spike (`docs/userstories/face-detail/`) showed that Hunyuan3D 2.1's shape model, not grid resolution, limits facial detail. A first backlog proposed Step1X-3D; the user then chose to try TRELLIS.2 first ("Probemos con Trellis2").

What exists (branch `feature/shape-models`, cut from `feature/face-detail`):

- The engine runs each stage in its own process and frees VRAM between stages in swap modes. A stage is a `module:factory` string loaded by `ProcessStageRunner`, which spawns the worker with `multiprocessing` and the engine's own interpreter (`stages/process.py`).
- UniRig lives in `vendor/UniRig` with its own `.venv` and is called as a subprocess (`stages/rig.py`), because its dependencies clash with the engine's.
- The cards are RTX 5070 (12 GB, compute capability 12.0, Blackwell), which needs torch built for CUDA 12.8 or newer. On this machine only the CUDA 13.2 `nvcc` builds torch extensions.
- `scripts/measure_stage_vram.py` and the bpy head renders from the face-detail spike give a repeatable comparison: same images (`marine`, `wolf`), seed 1234, peak VRAM, run time, head close-ups.

TRELLIS.2 facts (official README, checked 2026-10-29; nothing installed or run yet):

- MIT license for model and code; nvdiffrast and nvdiffrec keep NVIDIA's own licenses.
- One model, `microsoft/TRELLIS.2-4B` (4 billion parameters), that generates geometry and PBR textures (base color, roughness, metallic, opacity) and exports GLB. A separate script textures an existing shape. No geometry-only mode is documented.
- Requires at least 24 GB, verified only on A100 and H100. No CPU offload, quantization, or low-VRAM mode is documented; the only memory hints are `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` and lower resolutions (512³, 1024³, 1536³).
- Default stack: PyTorch 2.6.0 + CUDA 12.4, Linux only. Native dependencies: flash-attn (xformers as fallback), nvdiffrast, nvdiffrec, CuMesh, O-Voxel, FlexGEMM (Triton), OpenCV with OpenEXR. Blackwell is not mentioned.

Risks, stated before approval:

- Fitting 12 GB is unproven. 4B parameters in bf16 are about 8 GB of weights alone, before activations. Running at 512³ and loading its sub-models one at a time may fit; the README does not say. This story may end with "does not fit".
- Every native dependency must be rebuilt for torch with CUDA 12.8 or newer and sm_120.
- Because TRELLIS.2 also textures, it competes with Hunyuan shape and paint together, so the comparison is end to end.

Assumptions to confirm when approving:

- TRELLIS.2 is installed by hand into `vendor/TRELLIS.2/` with its own `.venv`, like UniRig. The setup wizard, the client, and the `.deb` are out of scope until a model wins.
- Before every download I ask the user, naming the source and approximate size.
- Hunyuan stays the default; a new model only runs when selected.
- If TRELLIS.2 cannot be built or does not fit 12 GB, the finding is recorded and Step1X-3D geometry (Apache-2.0, 1.3B) is proposed next in a revised backlog.

## US-01: Run TRELLIS.2 on one RTX 5070

As a developer, I want TRELLIS.2 working in its own environment on this machine, so that it can be compared with Hunyuan.

Acceptance criteria:

- US-01 / AC-01: `vendor/TRELLIS.2` has a `.venv` with a Blackwell-capable torch, and a short script outside the engine loads the model on one card and writes a textured GLB for an input image at 512³.
- US-01 / AC-02: The script records peak VRAM and run time. If the default load does not fit, it tries, in order: `expandable_segments`; keeping every sub-model in RAM and moving each to the GPU only while it runs, with each stage's small result kept on the CPU between stages; splitting the sub-models across both cards so none is reloaded; and, if one sub-model still does not fit, layer-by-layer offload of that sub-model. Each attempt, its peak VRAM, and its run time are recorded here. The user accepted slower runs to make it fit ("No importa si no cabe se podria cargar el resto en la RAM", 2026-10-29); the machine has 76 GB of RAM.
- US-01 / AC-03: If the environment cannot be built (torch, flash-attn or xformers, nvdiffrast, CuMesh, O-Voxel, FlexGEMM on CUDA 13 and sm_120) or no attempt fits 12 GB, the blocker and the failing output are recorded here and US-02 to US-04 stop for a decision.

Dependencies: user approval of each download. Verification: the GLB opens in Blender; peaks and install log recorded.

Results (2026-10-29, bus 0000:29:00.0, `pipeline_type="512"`, seed 1234, GLB decimated to about 145 000 faces with a 4096 px texture):

Environment as built in `vendor/TRELLIS.2/.venv` (Python 3.11):

- torch 2.9.0 + CUDA 13.0 with xformers 0.0.33.post1 (`ATTN_BACKEND=xformers`). torch 2.14 failed: its headers require C++20 and the extensions build with C++17.
- transformers 4.57.6. Version 5.x moved DINOv3's layers under `model.model`, which breaks TRELLIS.2's feature extractor.
- nvdiffrast v0.4.0, CuMesh, FlexGEMM, and o-voxel built with the CUDA 13.2 `nvcc` for `TORCH_CUDA_ARCH_LIST=12.0`. nvdiffrec failed to link; it is used only by the PBR preview renderer (`trellis2/renderers/pbr_mesh_renderer.py`), not by generation or GLB export.
- RMBG-2.0 is not downloaded: inputs are given with alpha (background removed by the engine's BiRefNet), which skips TRELLIS.2's own remover.

| Attempt | Image | Peak VRAM MiB | Load s | Generate s | Export s |
| --- | --- | ---: | ---: | ---: | ---: |
| `low_vram=True` (upstream default: each sub-model moves to the GPU only for its stage) | marine | 3 959 | 43.8 | 56.3 | 33.2 |
| `low_vram=True` | wolf | 3 747 | 46.0 | 44.7 | 28.5 |
| `low_vram=False` (all sub-models resident) | marine | out of memory at 11.4 GiB | | | |

- The user's staged idea is already TRELLIS.2's default mode, and it fits 12 GB with about 8 GiB to spare, so the later fallbacks (two cards, layer offload) were not needed.
- Compared with Hunyuan `high` (octree 512), TRELLIS.2's geometry is cleaner and sharper (nose, mouth, folds, collar, ears), with no voxel steps. It is less faithful to the input, though: the marine's head shape changes and his eyes come out closed, and the textures lose fine detail such as the scars, the forehead plug, and the wolf's chest emblem.

## US-02: Compare TRELLIS.2 with Hunyuan on faces

As a user, I want to see TRELLIS.2's faces next to Hunyuan's, so that I can decide whether it is worth integrating.

Acceptance criteria:

- US-02 / AC-01: For `marine` and `wolf`, TRELLIS.2's peak VRAM, load time, run time, and face count are recorded next to Hunyuan `high` (shape + paint).
- US-02 / AC-02: Head close-ups of both models, untextured and textured, rendered as in the face-detail spike, are sent to the user side by side.
- US-02 / AC-03: The user's verdict is recorded. If TRELLIS.2 loses, US-03 and US-04 are dropped and the next candidate is proposed in a revised backlog.

Dependencies: US-01. Verification: recorded numbers and the user's review.

Results (2026-10-29, after a machine reboot that cleared `/tmp`; spike files now live in `data/spikes/`, which git ignores). TRELLIS.2 ran on bus 0000:29:00.0 with `low_vram=True` and 1024 weights downloaded with the user's approval ("Sí, probá 1024"). Hunyuan `high` (octree 512) ran at the same time on bus 0000:25:00.0, so its times include contention and a cold cache.

| Model | Image | Peak VRAM MiB | Load s | Generate s | Export s | Faces |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Hunyuan `high` shape + paint | marine | 10 605 shape / 7 809 paint | 155 + 93 | 186 + 239 | (in paint) | 150 000 |
| Hunyuan `high` shape + paint | wolf | 10 267 shape / 7 897 paint | 94 + 59 | 181 + 212 | (in paint) | 150 000 |
| TRELLIS.2 `512` | marine | 3 937 | 60.0 | 33.7 | 38.2 | 144 171 |
| TRELLIS.2 `512` | wolf | 4 047 | 52.1 | 15.0 | 29.1 | 144 106 |
| TRELLIS.2 `1024_cascade` | marine | 10 690 (torch 6 933) | 296.9 (cold cache) | 118.6 | 41.2 | 149 037 |
| TRELLIS.2 `1024_cascade` | wolf | 4 695 (torch 3 234) | 81.5 | 47.4 | 34.6 | 147 268 |

- `1024_cascade` is clearly the best of the three on faces. Marine: hair strands, brow ridge, wrinkles, scars, and the forehead plug are sharp in both geometry and texture. Wolf: crisp yellow eyes, fur tufts, muzzle, and ears.
- Remaining flaw: the marine's eyes are still narrow and half closed, less open than in the input.
- `512` is much weaker than `1024_cascade`: blurrier texture, the marine's eyes closed, his head shape changed.
- Peak VRAM at `1024_cascade` depends on the subject: 10.7 GiB for the marine (dense surface) against 4.7 GiB for the wolf. It still fits one 12 GB card, with little margin on the marine.
- Head renders: `data/spikes/face/renders/cmp3-{marine,wolf}-{shape,tex}.png` (left Hunyuan `high`, middle TRELLIS.2 `512`, right TRELLIS.2 `1024_cascade`).
- User verdict: pending.

## US-03: Select the 3D model in the engine

As a user, I want to choose TRELLIS.2 or Hunyuan for generation, so that I can use the one that gives better faces.

Acceptance criteria:

- US-03 / AC-01: A TRELLIS.2 stage runs inside TRELLIS.2's own `.venv` on one card, keeps its model resident between jobs like the Hunyuan workers when memory allows, and writes the job's `model.glb`, so rigging works unchanged.
- US-03 / AC-02: An environment variable `MODEL3D_GENERATOR` (`hunyuan` by default, or `trellis2`) chooses the pipeline; an unknown value fails at startup with a clear message. `MODEL3D_TRELLIS_DIR` moves the TRELLIS.2 clone (default `vendor/TRELLIS.2`).
- US-03 / AC-03: When `trellis2` is chosen but `vendor/TRELLIS.2/.venv` is missing, jobs fail with a `StageError` that names the missing folder, as the rig stage does for UniRig.
- US-03 / AC-04: Timeouts, crashes, respawn, and unload behave as they do for the Hunyuan workers; existing engine tests pass, and new tests cover the selection and the missing-environment error with fake factories.
- US-03 / AC-05: How TRELLIS.2 maps onto the run modes (which card, what thresholds) is decided with the user after US-01's measurements, before this story is implemented.

Dependencies: US-02 verdict in favour. Verification: engine unit tests plus one real job per model on the GPU.

Decisions (2026-10-29):

- User verdict on US-02: integrate TRELLIS.2 `1024_cascade` ("Sí, integralo").
- AC-05, chosen by the user ("Una tarjeta"): TRELLIS.2 generates shape and texture on one card, the usable GPU with the most free VRAM, and rigging takes turns on that same card. The other card stays free; one job runs at a time. A card qualifies when its free VRAM is at least `TRELLIS_NEED_MIB` (10 690, the marine's `1024_cascade` peak) plus the usual 512 MiB margin; otherwise the engine starts in `unsupported` with the reason.
- Quality maps to the pipeline type: `draft` uses `512`, while `final` and `high` use `1024_cascade`. The face target matches Hunyuan's paint stage: 40 000 for `draft` and `final`, 150 000 for `high`.
- `MODEL3D_MODE`, `MODEL3D_SHAPE_BUS`, and `MODEL3D_PAINT_BUS` only apply to `hunyuan`; setting them with `MODEL3D_GENERATOR=trellis2` fails at startup instead of being ignored.
- How it runs: the engine keeps its `ProcessStageRunner` (timeouts, crash restart, unload) and loads a thin proxy stage in the engine's environment. The proxy removes the background with the engine's BiRefNet, then drives a persistent worker process started with `vendor/TRELLIS.2/.venv/bin/python`, which keeps the model in RAM with `low_vram` and talks JSON lines over a pipe. The worker exits when its parent dies, so unloading or killing the proxy frees the card.
- The scheduler skips paint when the shape stage reports a textured result; paint is a no-op runner in this mode.

Results (2026-10-29):

- The engine starts with `MODEL3D_GENERATOR=trellis2` and reports `single_gpu_resident`: "TRELLIS.2 generates shape and texture on 0000:29:00.0". The worker holds almost no VRAM while idle.
- The user's first job from the app (a full-body dog-headed marine with two weapons, `final`) failed at 90 % with `[CuMesh] CUDA error ... out of memory` in `o_voxel.postprocess.to_glb`. CuMesh allocates outside PyTorch's caching allocator, so memory PyTorch had cached during generation starved it.
- Fix in `trellis_worker.py`: empty the cache after generation, simplify the raw mesh to `RAW_FACES` (2 000 000) first, and retry without remeshing on a CuMesh out-of-memory error. After any failure, every sub-model moves back to RAM.
- The same job, rerun through the real stage with the same seed, succeeded in 120 s (62 s load), peak 9 516 MiB, 38 079 faces, textured. Remeshing was still used, so the fallback was not needed.
- 2026-10-30: the user's retry with the marine ("me sigue dando el mismos problema") failed at 80 %, again a CuMesh out-of-memory error, this time in `Mesh.fill_holes` during `decode_latent`, before the earlier fix applies. On one 12 GB card, `1024_cascade` sits within about 1 GiB of the limit for dense subjects, and the seed decides which side it lands on. The worker now empties the cache before `fill_holes`, skips the fill when it still runs out, and collects garbage after a failure.

Scope revision (2026-10-30). The user asked "Creo que se podrian usar las 2 gpus no ?" and chose option 1 ("1"): split each TRELLIS.2 job across both cards instead of one. This replaces the AC-05 decision "Una tarjeta":

- The generation card (the one with the most free VRAM) keeps the image encoder and the flow models: structure, shape, and texture.
- The second card decodes the latents and does all CuMesh work: hole filling, simplification, remeshing, and texture baking. The latents move to it between steps.
- Jobs still run one at a time, and rigging takes turns with generation. With only one usable card, the engine falls back to the single-card path with the guards above.
- The thresholds per card come from measuring the split on the marine, the densest test subject.

Split measurements (2026-10-30, marine, `final`, seed 181192836; peaks are MiB above each card's idle use):

| Run | Generation card | Decode + bake card | Run s | Result |
| --- | ---: | ---: | ---: | --- |
| One card, with the out-of-memory guards | 11 440 (both on 0000:29:00.0) | | 138 | OK, 39 151 faces; hole filling was not skipped |
| Two cards: generate on 0000:29:00.0, decode on 0000:25:00.0 | about 5 180 | about 10 120 | 152 | OK, 38 648 faces |

- Decoding and CuMesh baking, not the flows, are the heavy part (about 10.1 GiB). The split leaves the heaviest card with about 1.6 GiB of margin instead of 0.3 GiB.
- So decoding and baking go on the card with the most free VRAM; generation goes on the other. Thresholds: `TRELLIS_GEN_NEED_MIB = 5180` and `TRELLIS_POST_NEED_MIB = 10120`, each plus the 512 MiB margin.
- Rigging runs on the decode card; jobs, decoding, and rigging all take turns, so only one of them uses the GPUs at a time.

## US-04: Document the second generator

As a developer, I want the README and CLAUDE.md to describe the generator option and how to install it by hand, so that the setup is reproducible.

Acceptance criteria:

- US-04 / AC-01: README and CLAUDE.md list `MODEL3D_GENERATOR`, the `vendor/TRELLIS.2` layout, and the measured VRAM.

Dependencies: US-03. Verification: documentation review.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | Script writes a GLB that opens in Blender | Passed: marine and wolf GLBs imported and rendered with bpy |
| US-01 / AC-02 | Peak VRAM per load attempt | Passed: 3 959 MiB with low_vram; OOM without |
| US-01 / AC-03 | Blocker recorded if the build or fit fails | Not triggered (nvdiffrec failure recorded, not needed) |
| US-02 / AC-01 | VRAM and time table | Passed |
| US-02 / AC-02 | Head renders side by side | Passed: sent to the user |
| US-02 / AC-03 | User verdict recorded | Passed: integrate 1024_cascade |
| US-03 / AC-01..AC-05 | Engine tests and one GPU job per model | Engine suite 184 passed; one real TRELLIS.2 job passed through the stage; job through the app pending the user's retry |
| US-04 / AC-01 | Documentation review | Pending |
