# Hardware-Adaptive Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine detects each person's NVIDIA GPUs at startup and runs shape and paint in the best of five modes, reporting the mode in health.

**Architecture:** A spawned torch probe inventories the visible GPUs (the API process never imports torch). A pure function picks a mode from that inventory, measured per-stage VRAM constants, and overrides. `build_engine` wires runners and the scheduler for the chosen mode: two GPUs as today, one GPU with both workers resident, one GPU where the scheduler swaps workers between stage batches, shape only, or no workers with a startup error.

**Tech Stack:** Python 3.11, FastAPI, PyTorch 2.9.1+cu130 (worker and probe processes only), pydantic v2 (`contracts/`), pytest + anyio.

**Spec:** `docs/superpowers/specs/2026-10-21-hardware-adaptive-engine-design.md` (decisions 1-16 are binding).

## Global Constraints

- NVIDIA only. AMD, Apple Silicon, and CPU-only machines get mode `unsupported` with a clear reason.
- Mode names, exactly: `dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, `shape_only`, `unsupported`.
- New error codes, exactly: `texture_unavailable` (400, `POST /v1/jobs`), `mode_unavailable` (startup error in health `error`).
- Settings: `MODEL3D_MODE` (optional, one of the mode names except `unsupported`); `MODEL3D_SHAPE_BUS` and `MODEL3D_PAINT_BUS` become optional with no default, and setting only one is a startup error.
- Thresholds are sized for `final` quality, with a 1 GiB safety margin, as named constants in `engine/src/model3d_engine/gpu/modes.py`.
- Mode selection uses **free** VRAM measured once before any worker spawns.
- The API process never imports torch.
- On the owner's machine (2x RTX 5070, shape bus `0000:25:00.0` on PCIe 2.0 x2, paint bus `0000:29:00.0` on PCIe 3.0 x16), auto mode must give `dual_gpu` with today's placement and today's speed (shape about 77 s, paint about 60 s).
- Paint keeps DINOv2 on the CPU and VAE slicing in every mode.
- Code goes to the local LLM providers first (AGENTS.md); TDD with RED observed before GREEN; English artifacts; Conventional Commits with no AI attribution; stage paths explicitly, never `git add -A`.
- Unit tests: `cd engine && uv run --no-sync pytest -q`. GPU tests (`-m gpu`) need LM Studio and Ollama unloaded first.

## Review Focus

1. **`CUDA_VISIBLE_DEVICES` hides a card**: nvidia-smi still lists it, torch does not. The inventory must come from torch, and a hidden card must never be chosen (Task 2 test `test_probe_merge_ignores_cards_torch_cannot_see`).
2. **nvidia-smi missing or failing while torch sees a GPU**: the mode is still chosen, with PCIe bandwidth unknown, treated as 0 (Task 2 test `test_probe_merge_without_nvidia_smi`).
3. **The desktop card has less free VRAM than the other**: on two 12 GB cards where one has only 6 GiB free, the mode is `single_gpu_swap` on the free card, not `dual_gpu` (Task 3 test `test_desktop_card_too_busy_falls_back_to_swap`).
4. **Cancel of a job waiting for paint during a swap**: it ends `cancelled` and never loads paint for it alone (Task 5 test `test_swap_cancel_while_waiting_for_paint`).
5. **The probe hangs or crashes** (broken driver): after 60 s, or on a crash, the mode is `unsupported` with the probe's reason, and the API still answers (Task 2 test `test_probe_failure_reports_reason`, Task 6 test `test_unsupported_mode_serves_health`).

---

### Task 1: Measure per-stage VRAM

**Files:**
- Create: `scripts/measure_stage_vram.py`
- Modify: `docs/architecture.md` (section 2, "Stage placement" table)

**Interfaces:**
- Produces: the numbers Task 3 copies into `SHAPE_NEED_MIB` and `PAINT_NEED_MIB`, plus the CUDA context overhead per process (`CONTEXT_MIB`).

No unit test: this is a measurement script, run by hand on the owner's GPUs.

- [ ] **Step 1: Write the script.** For each stage (`shape`, then `paint` against the shape output), and each quality (`final`, `draft`), it starts one `ProcessStageRunner` on `cuda:1` (the x16 card), runs the penguin (`engine/tests/assets/penguin.png`), and records: the GPU's used MiB from nvidia-smi before spawn, after load, and at peak (sampled every 0.2 s during the run), and the time. It prints a table in Markdown. It also prints each card's idle free VRAM before anything loads (the x16 card drives the desktop), which Task 3 needs. It refuses to run if the card has less than 10 GiB free.
- [ ] **Step 2: Unload LM Studio and Ollama, then run it.** Run: `uv run --project engine python scripts/measure_stage_vram.py`. Expected: one row per stage and quality, with paint's peak about 8.7 GiB (the phase 1 spike).
- [ ] **Step 3: Run shape and paint resident on the same card** (both workers loaded on `cuda:1`, one job through both) and record whether it fits in 12 GB. Expected: it does not (about 17 GiB), confirming mode 2 needs a bigger card.
- [ ] **Step 4: Write the measured table into `docs/architecture.md` section 2**, replacing the "Peak VRAM" column's phase 1 numbers, and state the context overhead per worker.
- [ ] **Step 5: Commit**

```bash
git add scripts/measure_stage_vram.py docs/architecture.md
git commit -m "docs: measure per-stage VRAM for mode thresholds"
```

### Task 2: GPU probe

**Files:**
- Create: `engine/src/model3d_engine/gpu/probe.py`
- Test: `engine/tests/test_probe.py`

**Interfaces:**
- Consumes: `GpuLink`, `parse_nvidia_smi`, `NVIDIA_SMI_COMMAND`, `bus_id_from_props`, `normalize_bus_id` from `gpu/topology.py`.
- Produces:
  - `@dataclass(frozen=True) class GpuInfo: index: int; bus_id: str; name: str; memory_total_mib: int; memory_free_mib: int; capability: tuple[int, int]; supported: bool; bandwidth_gbps: float`
  - `@dataclass(frozen=True) class Inventory: gpus: tuple[GpuInfo, ...]; problem: str | None` (`problem` is set when there are no usable GPUs, in plain English, for example "No NVIDIA GPU is visible to PyTorch").
  - `def merge_inventory(torch_gpus: list[dict], links: list[GpuLink] | None) -> Inventory`: pure; `torch_gpus` are the probe's raw dicts (`index`, `bus_id`, `name`, `total_mib`, `free_mib`, `major`, `minor`, `supported`).
  - `def probe_gpus(timeout_s: float = 60.0) -> Inventory`: spawns `probe_main` in a `multiprocessing` spawn context with `CUDA_DEVICE_ORDER=PCI_BUS_ID`, reads nvidia-smi links (failure means `None`), and merges.
  - `def probe_main(conn) -> None`: runs in the child; imports torch; sends the list of dicts, or `("error", "<reason>")`.

- [ ] **Step 1: Write the failing tests** in `engine/tests/test_probe.py`:
  - `test_probe_merge_two_cards`: two torch dicts on `0000:25:00.0` and `0000:29:00.0`, links from the owner's nvidia-smi lines (as in `tests/test_main.py` `REAL`) → two `GpuInfo`s with `bandwidth_gbps` 1.0 and 15.76, `problem is None`.
  - `test_probe_merge_ignores_cards_torch_cannot_see`: links list both cards, torch lists only `0000:29:00.0` → one `GpuInfo`, index 0.
  - `test_probe_merge_without_nvidia_smi`: `links=None` → `bandwidth_gbps == 0.0`, still listed.
  - `test_probe_merge_no_gpus`: empty torch list → `gpus == ()` and `problem == "No NVIDIA GPU is visible to PyTorch"`.
  - `test_probe_merge_unsupported_capability`: a dict with `supported=False`, `major=6, minor=1` → listed with `supported is False`; when it is the only GPU, `problem` names it: `"GPU 0000:01:00.0 (sm_61) is not supported by this PyTorch build"`.
  - `test_probe_failure_reports_reason`: `probe_gpus` with a monkeypatched child target that raises → `problem` starts with `"GPU probe failed:"`; and with a target that sleeps, `timeout_s=1` → `problem == "GPU probe did not answer within 1 s"`.
- [ ] **Step 2: Run them.** Run: `cd engine && uv run --no-sync pytest tests/test_probe.py -q`. Expected: FAIL with `ModuleNotFoundError: model3d_engine.gpu.probe`.
- [ ] **Step 3: Implement `gpu/probe.py`.** In `probe_main`, `supported` is `f"sm_{major}{minor}" in torch.cuda.get_arch_list()` (PyTorch's own list, spec decision 10); free and total come from `torch.cuda.mem_get_info(index)`. Make the child target injectable (a module-level name the test can monkeypatch) so the failure tests do not need torch.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** (`feat(engine): probe GPUs through a spawned torch process`).

### Task 3: Mode selection

**Files:**
- Create: `engine/src/model3d_engine/gpu/modes.py`
- Test: `engine/tests/test_modes.py`

**Interfaces:**
- Consumes: `GpuInfo`, `Inventory` (Task 2).
- Produces:
  - `class ModeName(str, Enum)` with the five names.
  - Constants `SHAPE_NEED_MIB`, `PAINT_NEED_MIB`, `CONTEXT_MIB`, `MARGIN_MIB = 1024`, from Task 1. A stage fits a card when `free >= need + CONTEXT_MIB + MARGIN_MIB`; both stages fit when `free >= SHAPE_NEED_MIB + PAINT_NEED_MIB + 2 * CONTEXT_MIB + MARGIN_MIB`.
  - `@dataclass(frozen=True) class ModePlan: name: ModeName; texture: bool; reason: str; shape: GpuInfo | None; paint: GpuInfo | None`
  - `class ModeUnavailable(Exception)` with `.code` (`"mode_unavailable"`) and `.message`.
  - `def choose_mode(inventory: Inventory, forced: ModeName | None = None, shape_bus: str | None = None, paint_bus: str | None = None) -> ModePlan`

- [ ] **Step 1: Write the failing tests** with fake `GpuInfo`s (helper `gpu(bus, free_gib, total_gib=12, bandwidth=15.76, supported=True)`):
  - `test_owner_machine_is_dual_gpu`: `25:00.0` (x2, 1.0 GB/s) and `29:00.0` (x16, 15.76 GB/s), with the idle free VRAM Task 1 recorded for each card (the x16 card drives the desktop) → `dual_gpu`, shape `25:00.0`, paint `29:00.0`, `texture is True`. If the measured needs plus `CONTEXT_MIB` and the 1 GiB margin do not fit the desktop card's idle free VRAM, stop and report it: shrinking the margin or accepting a slower mode on the owner's machine is the owner's call (Global Constraints: today's placement and speed).
  - `test_one_24gb_card_is_resident`: one card, 23 GiB free → `single_gpu_resident`.
  - `test_one_12gb_card_swaps`: one card, 11 GiB free → `single_gpu_swap`, shape and paint on the same card.
  - `test_card_that_fits_shape_only`: free between the two needs (skip with `pytest.skip` if `PAINT_NEED_MIB <= SHAPE_NEED_MIB`, per spec decision 1) → `shape_only`, `texture is False`.
  - `test_too_small_and_no_gpu_are_unsupported`: one card with 4 GiB free → `unsupported`, reason mentions the free GiB; empty inventory → `unsupported`, reason is the inventory's `problem`.
  - `test_unsupported_card_is_skipped`: a supported 12 GB card plus an unsupported one → `single_gpu_swap` on the supported card.
  - `test_desktop_card_too_busy_falls_back_to_swap`: two 12 GB cards, one with 6 GiB free → `single_gpu_swap` on the other card.
  - `test_mixed_cards_use_most_free`: 12 GB with 11 GiB free and 6 GB with 5 GiB free → `single_gpu_swap` on the first.
  - `test_three_cards_use_two_best`: three qualifying cards → paint on the highest bandwidth, shape on the next.
  - `test_forced_modes`: `forced=dual_gpu` with one card → raises `ModeUnavailable` naming the GPU count; `forced=single_gpu_resident` on an 11 GiB card → returned (margin skipped, spec decision 14); `forced=shape_only` → `texture is False`.
  - `test_bus_overrides`: same bus for both → `single_gpu_swap`; same bus plus `forced=single_gpu_resident` → resident; a bus not in the inventory → `ModeUnavailable`.
  - Reasons are short English sentences that name the free GiB, for example `"One GPU with 11.0 GiB free: shape and paint take turns"`.
- [ ] **Step 2: Run them.** Expected: FAIL with `ModuleNotFoundError`.
- [ ] **Step 3: Implement `choose_mode`.** Order: overrides first, then `dual_gpu` over the two best qualifying cards (paint gets the higher bandwidth), then the single card with the most free VRAM through resident, swap, and shape-only, else `unsupported`.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** (`feat(engine): choose a run mode from the GPU inventory`).

### Task 4: Settings

**Files:**
- Modify: `engine/src/model3d_engine/config.py`
- Test: `engine/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.mode: str | None = None`, `Settings.shape_bus: str | None = None`, `Settings.paint_bus: str | None = None`. `load_settings` raises `ValueError("Set both MODEL3D_SHAPE_BUS and MODEL3D_PAINT_BUS, or neither")` when only one is set, and `ValueError` naming the allowed values for an unknown `MODEL3D_MODE`.

- [ ] **Step 1: Update `test_defaults`** (`shape_bus is None`, `paint_bus is None`, `mode is None`) **and add** `test_mode_and_bus_validation` (`MODEL3D_MODE=single_gpu_swap` accepted; `MODEL3D_MODE=unsupported` and `MODEL3D_MODE=fast` rejected; one bus only rejected with the exact message above).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** The `ModeName` values come from `gpu/modes.py`; do not copy the list.
- [ ] **Step 4: Run them, then the whole suite.** Expected: PASS. `test_main.py` and `test_hunyuan_gpu.py` callers of `resolve_placement(links, settings.shape_bus, settings.paint_bus)` break here; Task 6 replaces them, so mark them in this step with the owner buses passed explicitly to keep the suite green.
- [ ] **Step 5: Commit** (`feat(engine): make GPU placement settings optional and add MODEL3D_MODE`).

### Task 5: Swap scheduling and unloadable runners

**Files:**
- Modify: `engine/src/model3d_engine/stages/process.py`, `engine/src/model3d_engine/jobs/scheduler.py`, `engine/src/model3d_engine/stages/protocol.py` (StageRunner protocol)
- Test: `engine/tests/test_scheduler.py`, `engine/tests/test_process_runner.py`, `engine/tests/fakes.py`

**Interfaces:**
- Produces:
  - `StageRunner.unload() -> Awaitable[None]`: stops the worker but leaves the runner restartable with `start()` (unlike `stop()`, which is final). `ProcessStageRunner.unload` waits for any running task, then stops the process the same way `stop()` does, without setting `_stopping`.
  - `Scheduler(store, bus, shape, paint, swap: bool = False)`. With `swap=False`, behavior is unchanged. With `swap=True`, `start()` loads shape only, and one loop replaces the two: it drains the shape queue, then, if paint has work, unloads shape, loads paint, drains the paint queue, and swaps back only when shape has work. Never both runners loaded at once. `ready` in swap mode is true when the loaded runner is ready or a swap is in progress (the engine is not "not ready" mid-swap).
  - `FakeRunner` gains `unload()` and records `("load", name)` / `("unload", name)` in a shared `log` list passed to its constructor.

- [ ] **Step 1: Write the failing tests:**
  - `test_swap_batches_by_stage` (scheduler, fakes): three textured jobs submitted together → log is `load shape, unload shape, load paint` and all three jobs succeed; no point in the log has both loaded.
  - `test_swap_back_when_new_shape_work_arrives`: a fourth job submitted while paint runs waits for the paint batch, then `unload paint, load shape`; its result is textured.
  - `test_swap_untextured_jobs_never_load_paint`: two `texture: false` jobs → paint never loaded.
  - `test_swap_cancel_while_waiting_for_paint`: cancel a job after shape and before the swap → `cancelled`, and if it was the only paint job, paint is never loaded.
  - `test_swap_paint_load_failure_fails_waiting_jobs`: paint `start()` raises `StageError("worker_start_failed", ...)` every time → waiting jobs fail with `worker_unavailable`, then shape work still runs.
  - `test_unload_then_start_reuses_runner` (process runner, `tests.worker_stubs:EchoStage`): `start`, `unload` (process gone, `ready is False`), `start`, `run` succeeds.
  - Existing scheduler tests with `swap=False` stay green unchanged.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `_run_stage`, `_begin`, `_fail`, `_cancel`, `_succeed` shared between both loops; the swap loop calls the same helpers.
- [ ] **Step 4: Run the whole suite.** Expected: PASS.
- [ ] **Step 5: Commit** (`feat(engine): swap stage workers on one GPU between stage batches`).

### Task 6: Wire modes into the engine, health, and the API

**Files:**
- Create: `contracts/src/model3d_contracts/health.py`, `contracts/tests/test_health.py`
- Modify: `engine/src/model3d_engine/__main__.py`, `engine/src/model3d_engine/api/app.py`, `engine/src/model3d_engine/jobs/scheduler.py`, `engine/src/model3d_engine/gpu/topology.py`, `engine/tests/test_main.py`, `engine/tests/test_api.py`, `engine/tests/test_hunyuan_gpu.py`

**Interfaces:**
- Consumes: `probe_gpus`, `Inventory` (Task 2); `choose_mode`, `ModePlan`, `ModeUnavailable` (Task 3); `Settings.mode/shape_bus/paint_bus` (Task 4); `Scheduler(swap=...)` (Task 5); `Scheduler.start_error` (commit `5c977e0`).
- Produces:
  - Contracts: `HealthMode(name: str, texture: bool, reason: str)`, `HealthGpu(bus_id: str, stage: Literal["shape", "paint", "shape+paint"] | None, ready: bool, memory_used_mib: int | None, memory_total_mib: int | None, memory_free_mib: int | None)`, `Health(ready: bool, mode: HealthMode, gpus: list[HealthGpu], error: JobError | None)`.
  - `build_engine(settings, inventory: Inventory | None = None, shape_factory=..., paint_factory=..., memory_reader=...)`: `inventory=None` calls `probe_gpus()`. It replaces the `links` parameter.
  - `resolve_placement` is removed; `Placement` is built from the `ModePlan` (CUDA device `f"cuda:{gpu.index}"`, helpers `"cpu"`).
  - `create_app(..., texture_available: bool)`: `POST /v1/jobs` answers `400 texture_unavailable` ("This engine runs in shape_only mode: send texture false") after options parse and before readiness, when texture is requested and unavailable.
  - Scheduler: when `start_error` is already set before `start()`, the lifespan does not start the scheduler.

- [ ] **Step 1: Write the failing tests:**
  - `contracts/tests/test_health.py`: a health dict as below round-trips through `Health.model_validate` and back.
  - `test_build_engine_dual_gpu_health` (replaces the existing health test in `test_main.py`): fake inventory of the owner's two cards → health is exactly `{"ready": True, "mode": {"name": "dual_gpu", "texture": True, "reason": ...}, "gpus": [{"bus_id": "0000:25:00.0", "stage": "shape", ..., "memory_free_mib": ...}, {"bus_id": "0000:29:00.0", "stage": "paint", ...}], "error": None}`, and a job succeeds.
  - `test_build_engine_swap_mode_runs_a_textured_job`: one fake card with 11 GiB free → `mode.name == "single_gpu_swap"`, one `gpus` entry with stage `"shape+paint"`, a textured job succeeds through the stub workers.
  - `test_unsupported_mode_serves_health`: empty inventory → health `ready` false, `mode.name == "unsupported"`, `error.code == "mode_unavailable"` and `error.message` is the inventory's problem; `POST /v1/jobs` answers 503 `engine_not_ready` naming it.
  - `test_forced_mode_unavailable_is_reported`: `Settings(mode="dual_gpu")` with one card → same as above with the `ModeUnavailable` message.
  - `test_shape_only_refuses_texture` (`test_api.py`): `texture_available=False` → default options answer `400 texture_unavailable`; `{"texture": false}` answers 202.
  - Update `test_hunyuan_gpu.py` to build placement from `choose_mode(probe_gpus())`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** `get_health` builds the body through the contracts `Health` model and returns `model_dump(mode="json")`. Unused cards appear with `stage: null`.
- [ ] **Step 4: Run engine and contracts suites.** Run: `cd engine && uv run --no-sync pytest -q` and `cd contracts && uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Update docs:** `docs/api.md` (health body, `texture_unavailable`, `mode_unavailable`, the five modes), `docs/architecture.md` (mode selection, thresholds, swap scheduling; replace the fixed-bus placement paragraph), `docs/setup.md` (`MODEL3D_MODE`, optional bus overrides). `scripts/verify_gpus.py` prints the chosen mode and reason after the placement section.
- [ ] **Step 6: Commit** (`feat(engine): pick the run mode from the detected GPUs`).

### Task 7: GPU acceptance on the owner's machine

**Files:**
- Modify: `engine/tests/test_hunyuan_gpu.py`

- [ ] **Step 1: Add `test_penguin_swap_mode_on_one_card`** (marked `gpu`): sets `CUDA_VISIBLE_DEVICES` to the x16 card's bus ID for the probe and the workers, asserts `single_gpu_swap`, runs the penguin with texture, asserts a textured GLB, and prints load, shape, swap, and paint times.
- [ ] **Step 2: Unload LM Studio and Ollama, then run the GPU suite.** Run: `cd engine && uv run --no-sync pytest -m gpu -s -q`. Expected: both GPU tests pass; the dual-GPU run prints shape about 77 s and paint about 60 s (no slower than phase 2).
- [ ] **Step 3: Record the timings** in `docs/architecture.md` (mode 1 and mode 3 rows) and in the ODD task log.
- [ ] **Step 4: Commit** (`test(engine): accept dual-GPU and swap modes on real GPUs`).
