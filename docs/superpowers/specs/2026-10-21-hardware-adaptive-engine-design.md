# Hardware-Adaptive Engine: Design

Date: 2026-10-21. Status: draft for owner approval.

## Intent

The owner needs 3D models for a project, the options they found cost money, and they plan to share this app with other people. Today the engine only runs on the owner's machine: two RTX 5070 cards (12 GB each), with shape and paint pinned to fixed PCI bus IDs (`0000:25:00.0`, `0000:29:00.0`).

The owner's request: "que se adapte al hardware que tiene cada persona". The engine must detect each person's GPUs at startup and choose the best way to run the pipeline on them, without anyone editing environment variables.

This comes before phase 3 (the PySide6 desktop app). The owner chose the order: adaptive engine first, then the app. The app will read the mode the engine chose and adapt its UI to it.

## Goals

1. On startup, the engine detects the NVIDIA GPUs (count, VRAM, PCIe link) and picks a run mode by itself.
2. The modes, from best to worst (approved by the owner):
   1. **Two GPUs** big enough for one stage each: today's behavior. Shape on one card, paint on the other, so job N paints while job N+1 shapes.
   2. **One GPU that holds both stages at once**: both workers resident on the same card, jobs run one after another.
   3. **One GPU that holds one stage at a time**: shape and paint take turns on the card (load one, unload it, load the other). Slower, but the result is textured.
   4. **One GPU that holds shape but not paint**: shape only, no texture.
   5. **No usable GPU** (no NVIDIA GPU, a GPU too old for the installed PyTorch and CUDA, or too little VRAM for shape): the engine does not load and explains why.
3. `GET /v1/health` reports the chosen mode and the reason in plain words, so the app can say, for example, "Your GPU has 8 GB: models without texture only".
4. Manual override still works: the existing `MODEL3D_SHAPE_BUS` and `MODEL3D_PAINT_BUS` force a placement, and a new `MODEL3D_MODE` forces a mode.
5. The owner's machine keeps today's behavior and speed (mode 1, shape on the x2 card, paint on the x16 card).

## Non-goals

- AMD GPUs, Apple Silicon, and CPU-only generation. Hunyuan3D 2.1 paint needs CUDA (the `custom_rasterizer` extension), so these get mode 5 with a clear message.
- Windows and macOS support, and packaging the engine for other people (phase 5).
- Lowering the quality or resolution to make a stage fit a smaller GPU. Modes decide where and when stages run, not what they produce.
- Speed optimizations such as moving weights to system RAM instead of reloading them (phase 5 lists RAM offload; it can come later as a faster mode 3).

## Anti-goals

- Choosing a mode that then fails with out-of-memory on the first job. A mode must be safe for the measured peak VRAM, with margin.
- Silently changing what a job produces. If texture is unavailable, a job that asks for texture is refused with a clear error, never returned untextured.
- Slowing down the owner's two-GPU machine.

## Constraints and known facts

Measured on the owner's machine (`docs/architecture.md`, section 2):

| Stage | Peak VRAM | Notes |
| :--- | :--- | :--- |
| Shape, resident | 7.6 GiB allocated, 8.7 GiB held by the worker | 77 s per job |
| Paint diffusion and VAE (one view at a time) | 8.7 GiB (phase 1 spike) | 58 s per job; DINOv2 on the CPU |
| Worker load at engine start | | about 51 s for both workers |

- Upstream quotes about 10 GB for shape and 21 GB for paint; this engine's paint fits in about 8.7 GiB because DINOv2 runs on the CPU and the VAE decodes one view at a time. These settings stay in every mode.
- The card that drives the desktop has less free VRAM (about 1.6 GB taken on the owner's GPU1). Mode selection must use **free** VRAM at startup, not total VRAM.
- Each stage already runs in its own spawned worker process (`stages/process.py`), and killing a process is the only reliable way to return all of its VRAM.
- Placement is resolved by PCI bus ID with `CUDA_DEVICE_ORDER=PCI_BUS_ID` (`gpu/topology.py`). `resolve_placement` currently refuses shape and paint on the same GPU; modes 2 and 3 need that.
- The engine environment is PyTorch 2.9.1+cu130 with CUDA 13.1. Which compute capabilities that build runs on must be checked against PyTorch's build (`torch.cuda.get_arch_list()`), not assumed.
- Only mode 1 and mode 3 can be tested on real hardware here (mode 3 by hiding one card with `CUDA_VISIBLE_DEVICES`). Modes 2, 4, and 5 are tested with fake GPU inventories in unit tests.

## Decisions (the owner's)

- The five modes above, in that order, and that the engine picks one automatically.
- Health tells the app which mode is running and why, in plain words.
- NVIDIA only; other hardware gets a clear "not supported" message.
- Adaptive engine first, desktop app second.

## Decisions (the author's calls, open to change)

1. **Thresholds come from measurement, not guesses.** The first task measures peak VRAM per stage (shape and paint, `final` and `draft` quality) on the owner's GPUs and writes the numbers to `docs/architecture.md`. Mode thresholds are those peaks plus a safety margin, kept as named constants in one place. The owner set the margin to 512 MiB after the measurement (paint peaks at 10168 MiB and the desktop card has 11173 MiB free, so 1 GiB would have moved paint off it). If the measurement shows paint needs no more than shape, mode 4 never triggers on its own and stays only as a forced mode; that is fine.
2. **Mode 3 swaps by process, not by moving weights.** Stopping one worker process and starting the other frees VRAM completely; moving Hunyuan's pipelines to the CPU risks leaks through references held across the pipeline. A swap costs a worker load (about 25 s per stage, to be measured).
3. **Mode 3 batches by stage.** The scheduler runs shape for every queued job, then swaps once to paint for all of them, instead of swapping twice per job. A job's total time grows, but the queue finishes sooner.
4. **Mode 1 placement rule on any machine:** among GPUs with enough free VRAM for one stage, paint goes to the card with the highest PCIe bandwidth and shape to the next one (shape sends only an image and a mesh per job, which is why the slow x2 card suits it). On the owner's machine this gives today's placement. With more than two GPUs, the extra cards are unused.
5. **Texture unavailable is an error.** In mode 4, `POST /v1/jobs` with `"texture": true` (the default) answers `400 texture_unavailable`. The app will send `"texture": false` when health says texture is unavailable.
6. **Mode 5 keeps the API up.** The engine still serves health (with `ready: false`, the mode, and the reason in `error`, using the startup error field added in commit `5c977e0`), so the app can show the message instead of failing to connect.
7. **Health shape (proposal):**

   ```json
   {
     "ready": true,
     "mode": {"name": "single_gpu_swap", "texture": true, "reason": "One GPU with 11.2 GiB free: shape and paint take turns"},
     "gpus": [...],
     "error": null
   }
   ```

   Mode names: `dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, `shape_only`, `unsupported`.
8. **Bus ID defaults go away.** `MODEL3D_SHAPE_BUS` and `MODEL3D_PAINT_BUS` become optional (unset means automatic). Setting only one of them is a startup error.
9. This work builds on branch `claude/peaceful-dirac-89df24` (commit `5c977e0`), because it uses the health `error` field.

10. **Detection runs once, before any worker spawns**, in a short spawned probe process (the API process never imports torch). It reads, per GPU: bus ID, name, PCIe link, total and free VRAM, compute capability, and whether `torch.cuda.get_arch_list()` covers that capability. Support comes from PyTorch's own arch list, not a hardcoded table. Worker restarts reuse the chosen mode; if another app later takes VRAM, the job fails with `gpu_out_of_memory` as today.
11. **Mode thresholds are sized for `final` quality**, the larger case. There is one set of thresholds, not one per job. Mode 2's threshold is the two workers' measured held memory (including each process's CUDA context) plus the margin. If that is more than a 24 GB card has free, such a card simply gets mode 3. Mode 2 is only ever chosen from measured numbers, and it is covered by unit tests with fake inventories, not by real hardware.
12. **Mixed GPUs:** if no two cards qualify for mode 1, the engine uses the card with the most free VRAM and ignores the rest. Two small cards are never combined for one stage.
13. **Mode 3 scheduling:** the active stage drains its whole queue, then the card swaps to the other stage if that stage has work. A job that arrives while paint runs waits for the paint batch to finish; there is no swap back mid-batch. `queue_position` keeps its meaning (position among jobs waiting for shape). A cancel during a swap applies at the job's next stage boundary, as today. If the next worker fails to load after a swap, the restart path (0, 5, 30, 120 s) retries it; jobs waiting for that stage fail with `worker_unavailable` if every retry fails.
14. **Forced modes:** `MODEL3D_MODE` that the hardware cannot provide at all (for example `dual_gpu` with one GPU) is a startup error, shown in health `error` with code `mode_unavailable`. A forced mode skips the VRAM margin check (the person takes the out-of-memory risk) but not the GPU count. `MODEL3D_SHAPE_BUS` and `MODEL3D_PAINT_BUS` with the same value mean mode 3, unless `MODEL3D_MODE=single_gpu_resident` is also set. Bus overrides and `MODEL3D_MODE` can be combined.
15. **Health and contracts:** in mode 5, `ready` stays `false` and `error` explains why. `mode.texture` comes from the mode, not from worker state. Each `gpus` entry gains `memory_free_mib` (at detection) and `stage` becomes `"shape"`, `"paint"`, `"shape+paint"`, or `null` (unused). The health body gets a typed model in `contracts/`, because the desktop app will parse it.
16. **Error order in `POST /v1/jobs`:** `texture_unavailable` is checked after the image and options are validated, and before `engine_not_ready`. So a valid textured request gets 400 in mode 4, even while the workers load.

## Left to the builder

- How the detector reads free VRAM and compute capability (nvidia-smi fields, or a short torch probe in a spawned process; the API process must not import torch).
- How the scheduler expresses swap batching, as long as cancel, queue position, and events keep their current behavior.
- The exact wording of mode reasons, kept short and in English (the app translates them).
- How `scripts/verify_gpus.py` shows the chosen mode.
- How the measurement task is run (it needs both GPUs free; unload LM Studio and Ollama first).

## Done when

- Unit tests cover mode selection for fake inventories: 2x12 GB (mode 1, today's placement), 1x24 GB (mode 2), 1x12 GB (mode 3), a GPU that fits shape but not paint (mode 4), no GPU and an unsupported GPU (mode 5), the forced modes, and invalid overrides.
- Mode 3 runs end to end on the owner's machine with one card hidden, and a textured GLB comes out.
- Mode 1 on the owner's machine is no slower than before (shape about 77 s, paint about 60 s).
- `docs/api.md`, `docs/architecture.md`, and `docs/setup.md` describe the modes, the new settings, `texture_unavailable`, and the health fields.
