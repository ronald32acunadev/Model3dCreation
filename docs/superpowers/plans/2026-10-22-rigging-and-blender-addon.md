# Rigging and Blender Add-on Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jobs can return a model with a skeleton (UniRig), existing library models can get one, the desktop app offers both, and a Blender 5.2 add-on generates, rigs, and imports models through the running engine.

**Architecture:** Rigging is a third engine stage, `rig`, run by `RigRunner`: one subprocess per task with UniRig's own interpreter (`vendor/UniRig/.venv/bin/python`) executing `model3d_engine/stages/unirig_job.py`, so UniRig's dependencies never enter the engine environment and no rig model stays resident. The scheduler serializes work per GPU with one lock per card; before a rig task, if the card lacks free VRAM, it parks (unloads) the worker sharing that card and reloads it before that worker's next task. The add-on is stdlib-only Python talking HTTP to `127.0.0.1:8765` from a background thread polled by a Blender timer.

**Tech Stack:** Python 3.11 (engine, client, contracts; uv), FastAPI, PySide6, pydantic 2, UniRig `6793c66` in its own venv (torch 2.9.1+cu130, flash-attn 2.8.3 built for sm_120), Blender 5.2.2 LTS with bundled Python 3.13.13.

**Spec:** `docs/superpowers/specs/2026-10-22-rigging-and-blender-addon-design.md`

## Global Constraints

- English for code, identifiers, comments, docs, commit messages; user-facing text through translation catalogs (Qt `.ts` for the client, `bpy.app.translations` for the add-on), English plus Spanish.
- Conventional Commits, no AI attribution lines (owner's global rule).
- Source code and tests are synthesized by the local LLM (`pumbastudio`, then `pumbalama`), with the host model as fallback (`AGENTS.md`), each request carrying a Library Context Brief.
- The API process never imports torch; UniRig never runs in the engine's environment.
- UniRig is an optional install: the engine runs without it and reports rigging unavailable.
- `weights_only=False` is forbidden; the skin checkpoint loads through `torch.serialization.add_safe_globals([box.Box])` applied by the engine's wrapper, never by editing `vendor/UniRig`.
- Never run `pip install` at job time (UniRig's `extract.sh` does; call its Python modules directly).
- Jobs without `rig` keep their current behavior, timings, and output.
- Rig jobs cap the mesh at 150,000 faces (or `target_faces`): `RIG_FACES = 150_000`.
- VRAM for one rig task: measured peak 5514 MiB; `RIG_REQUIRED_MIB = 6100` (peak plus the engine's 512 MiB margin, rounded).
- Blender add-on: stdlib only, Blender extension format (`blender_manifest.toml`, `blender_version_min = "4.2.0"`), never starts or stops the engine; no network or file call on Blender's main thread except the final GLB import.
- Rigging an existing model creates a new library entry; the original entry is never modified.
- Library entry format is the client's `Entry` model (`client/src/model3d_client/services/library.py`): folder `data/library/<YYYYmmddTHHMMSS>-<6 hex>/` with `entry.json`, `input.<ext>`, `model.glb`.

## Review Focus

1. UniRig env or weights missing while the engine runs: health says `rig: false` with a reason, the app's "With skeleton" and "Add skeleton" are disabled showing that reason, and a job with `rig: true` gets `400 rig_unavailable` (tests in Task 4 and Task 7).
2. A rig-only upload that is not a GLB, is truncated, or is over the size limit: `400 invalid_model` or `413 model_too_large`, never a stuck job (Task 4).
3. Paint parked to make room for a rig task: health stays `ready: true`, and the next textured job reloads paint and succeeds (Task 3).
4. The engine disappears mid-job in Blender (desktop app closed): the add-on stops polling within a few seconds and shows "Open the Model3D desktop app" instead of hanging (Task 8).
5. An entry written by the add-on, including a half-written one, never breaks the desktop app's library and shows up when the app window regains focus (Task 7, Task 8).

---

### Task 1: Contracts for rigging

**Files:**
- Modify: `contracts/src/model3d_contracts/jobs.py`, `contracts/src/model3d_contracts/health.py`
- Test: `contracts/tests/test_contracts.py`, `contracts/tests/test_health.py`

**Interfaces:**
- Produces: `JobOptions.rig: bool = False`; `JobState.rigging = "rigging"`; `Stage = Literal["shape", "paint", "rig"]`; `Health.rig: bool = False` and `Health.rig_reason: str | None = None` (defaults keep older bodies valid).

- [ ] **Step 1: Write failing tests** `test_job_options_rig_defaults_false` (`JobOptions().rig is False`, `JobOptions.model_validate({"rig": True}).rig is True`), `test_rigging_state_and_rig_stage` (`JobState("rigging")`, `Job(..., state="rigging", stage="rig")` validates), `test_health_rig_fields_default` (a body without `rig` parses with `rig is False`, `rig_reason is None`; `{"rig": False, "rig_reason": "UniRig environment missing"}` round-trips).
- [ ] **Step 2: Run** `cd contracts && uv run pytest -q` — expect the three new tests to FAIL.
- [ ] **Step 3: Add the fields and enum values** listed under Interfaces.
- [ ] **Step 4: Run** `cd contracts && uv run pytest -q` — all pass; then `cd engine && uv run pytest -q` and `cd client && uv run pytest -q` still pass.
- [ ] **Step 5: Commit** `feat(contracts): add rig option, rigging state, and rig health fields`.

### Task 2: UniRig job script and RigRunner

**Files:**
- Create: `engine/src/model3d_engine/stages/unirig_job.py` (runs under UniRig's interpreter; imports only stdlib at module level)
- Create: `engine/src/model3d_engine/stages/rig.py`
- Create: `engine/tests/unirig_stub.py` (a fake "UniRig python" entry used by tests), `engine/tests/test_rig_runner.py`
- Modify: `engine/src/model3d_engine/config.py` (`unirig_dir`), `engine/tests/test_config.py`

**Interfaces:**
- Consumes: `StageTask`, `StageResult`, `StageError`, `ProgressCallback`, `MODEL_OUTPUT` from `stages/protocol.py`.
- Produces:
  - `Settings.unirig_dir: Path` (env `MODEL3D_UNIRIG_DIR`, default `repo_root / "vendor/UniRig"`).
  - `RIG_INPUT = "rig_input.glb"` in `stages/protocol.py`: the file a rig task reads from the job folder.
  - `rig_availability(unirig_dir: Path, hf_home: Path) -> tuple[bool, str | None]` in `stages/rig.py`: false with a reason when `unirig_dir/.venv/bin/python`, `unirig_dir/run.py`, or either checkpoint (`hf_home/hub/models--VAST-AI--UniRig/snapshots/*/skeleton/articulation-xl_quantization_256/model.ckpt`, `.../skin/articulation-xl/model.ckpt`) is missing. Reasons: `"UniRig is not installed in vendor/UniRig (see docs/setup.md)"`, `"UniRig's environment is missing (see docs/setup.md)"`, `"UniRig weights are missing from data/hf (see docs/setup.md)"`.
  - `class RigRunner` (satisfies `StageRunner`): `__init__(self, unirig_dir: Path, hf_home: Path, device: str, free_mib: Callable[[], int | None], python: Path | None = None, script: Path | None = None, task_timeout_s: float = 900.0)`; `ready` is the availability result; `start()` / `unload()` / `stop()` are no-ops that keep `ready` (nothing stays loaded); `async fits() -> bool` is `free_mib() is None or free_mib() >= RIG_REQUIRED_MIB`; `async run(task, on_progress) -> StageResult` reads `task.job_dir / RIG_INPUT`, writes `task.job_dir / MODEL_OUTPUT`.
  - `unirig_job.py` CLI: `python unirig_job.py --unirig-dir DIR --input GLB --output GLB --workdir DIR --max-faces N`. Prints `PROGRESS <0..1> <message>` lines, `RESULT <vertices> <faces>` last, exits non-zero with `ERROR <code> <message>` on failure (codes: `rig_failed`, `gpu_out_of_memory`).

- [ ] **Step 1: Write failing tests in `test_rig_runner.py`**, with `unirig_stub.py` standing in for the UniRig interpreter (the runner takes `python=sys.executable`, `script=tests/unirig_stub.py`; the stub reads env `STUB_MODE`):
  - `test_rig_runner_forwards_progress_and_returns_result`: stub prints `PROGRESS 0.4 Skeleton ready`, copies input to output, prints `RESULT 8 12`; assert `on_progress` saw `(0.4, "Skeleton ready")`, result `vertices == 8`, `faces == 12`, `output == job_dir / "model.glb"`.
  - `test_rig_runner_maps_error_line_to_stage_error`: stub prints `ERROR gpu_out_of_memory CUDA out of memory` and exits 1 → `StageError` with `code == "gpu_out_of_memory"`.
  - `test_rig_runner_crash_without_error_line_is_rig_failed`: stub exits 3 silently → `StageError(code="rig_failed")`, message contains the last stderr line.
  - `test_rig_runner_timeout_kills_process`: stub sleeps; `task_timeout_s=0.5` → `StageError(code="stage_timeout")` and no child process left.
  - `test_rig_runner_sets_cuda_and_hf_env`: stub writes `CUDA_VISIBLE_DEVICES` and `HF_HOME` to a file; device `"cuda:1"` gives `CUDA_VISIBLE_DEVICES == "1"` and `HF_HOME == str(hf_home)`.
  - `test_fits_compares_free_vram`: `free_mib=lambda: 6099` → False; `6100` → True; `None` → True.
  - `test_rig_availability_reports_each_missing_piece`: build fake trees under `tmp_path` for each of the three reasons and the all-present case.
  - In `test_config.py`: `test_unirig_dir_default_and_override`.
- [ ] **Step 2: Run** `cd engine && uv run pytest tests/test_rig_runner.py tests/test_config.py -q` — expect FAIL (module missing).
- [ ] **Step 3: Implement `RigRunner`** with `asyncio.create_subprocess_exec`, `cwd=unirig_dir`, env adding `CUDA_VISIBLE_DEVICES` (the index from `device`), `HF_HOME`, and `PATH` with the venv's `bin`; parse stdout line by line; on timeout kill the process group. Add `Settings.unirig_dir`.
- [ ] **Step 4: Implement `unirig_job.py`**: replicate the commands of UniRig's `launch/inference/generate_skeleton.sh`, `generate_skin.sh`, and `merge.sh` with their default arguments (skeleton task `configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml`, skin task `configs/task/quick_inference_unirig_skin.yaml`, `faces_target_count=50000`, seed `12345`), calling `python -m src.data.extract`, `run.py`, and `python -m src.inference.merge` directly, all intermediates under `--workdir`. Run `run.py` through a bootstrap (`python -c` that calls `torch.serialization.add_safe_globals([box.Box])`, then `runpy.run_path("run.py", run_name="__main__")` with `sys.argv` set). Before rigging, when the input mesh has no texture and more than `--max-faces` faces, decimate it with `fast_simplification` (installed with UniRig). Map `torch.OutOfMemoryError` text in a step's stderr to `gpu_out_of_memory`, anything else to `rig_failed`. Count vertices and faces of the output with `trimesh`.
- [ ] **Step 5: Run** the Step 2 command — all pass.
- [ ] **Step 6: Smoke the real script once** (GPUs free): `vendor/UniRig/.venv/bin/python engine/src/model3d_engine/stages/unirig_job.py --unirig-dir vendor/UniRig --input data/library/20261022T035105-213863/model.glb --output <scratch>/out.glb --workdir <scratch>/work --max-faces 150000` with `HF_HOME=data/hf CUDA_VISIBLE_DEVICES=1` — expect `RESULT` and a GLB that Blender imports with one armature. Scratch dir from `mktemp -d`, removed afterwards.
- [ ] **Step 7: Commit** `feat(engine): run UniRig rigging in its own process`.

### Task 3: Scheduler rig stage, card locks, and parking

**Files:**
- Modify: `engine/src/model3d_engine/jobs/scheduler.py`, `engine/src/model3d_engine/jobs/store.py`, `engine/src/model3d_engine/stages/hunyuan.py`
- Test: `engine/tests/test_scheduler.py`, `engine/tests/test_store.py`, `engine/tests/test_quality_presets.py`, `engine/tests/fakes.py`

**Interfaces:**
- Consumes: `RigRunner`-shaped runner with `async fits() -> bool` (Task 2), `RIG_INPUT`.
- Produces:
  - `Scheduler(store, bus, shape, paint, swap=False, rig: StageRunner | None = None, cards: dict[str, str] | None = None)`. `cards` maps `"shape"`, `"paint"`, `"rig"` to a device; runners on the same device share one `asyncio.Lock`, held for the whole `run()`. Default `cards=None` means every stage has its own lock (today's behavior).
  - `Scheduler.submit_rig(record: JobRecord) -> None`: queues a job straight to the rig stage.
  - `Scheduler.rig_available: bool` (`rig is not None and rig.ready`).
  - `JobStore.create_rig(model_bytes: bytes, textured: bool) -> JobRecord`: writes `rig_input.glb`, options `JobOptions(rig=True, texture=textured)`.
  - `JobRecord` state machine: `queued → rigging`, `shaping → rigging`, `painting → rigging`, `rigging → succeeded | failed | cancelled`; `transition(rigging)` sets `stage="rig"`, `progress=0.0`.
  - `shape_face_target(options: JobOptions) -> int | None` in `stages/hunyuan.py`: `target_faces`, else `RIG_FACES` when `rig and not texture`, else `None`; `ShapeStage.run` uses it instead of reading `target_faces` directly.
  - `FakeRunner` gains `fits_result: bool = True` and `async fits()`.
- Behavior: after shape (untextured) or paint (textured), a job with `rig` moves its result to `RIG_INPUT` and queues for rig; otherwise unchanged. Rig queue is drained by its own loop (non-swap) or as a third stage in the swap loop. Before a rig task: `if not await rig.fits()`, park the runners on the rig card (non-swap: the ones in `cards` sharing the rig device; swap: the loaded runner), holding the card lock. A parked runner counts as ready for `Scheduler.ready`, and is started again under the card lock before its next task. Success stats for a rigged job use `textured=record.options.texture`.

- [ ] **Step 1: Write failing tests:**
  - `test_untextured_rig_job_runs_shape_then_rig` (states `shaping`, `rigging`, `succeeded`; rig fake ran once; paint never ran).
  - `test_textured_rig_job_runs_all_three_stages_in_order`.
  - `test_rig_only_job_skips_shape_and_paint` (`store.create_rig(b"glb", True)` + `submit_rig`; completed stats `textured is True`).
  - `test_rig_never_overlaps_paint_on_shared_card` (cards `{"shape": "cuda:0", "paint": "cuda:1", "rig": "cuda:1"}`, delays 0.05; no overlap between paint and rig `runs` intervals; shape may overlap rig).
  - `test_rig_parks_paint_when_card_is_full_and_ready_stays_true` (rig `fits_result=False`; log shows `("unload", "model.glb")` before the rig run; `scheduler.ready` is True during the rig run; a following textured job logs `("load", "model.glb")` and succeeds).
  - `test_swap_mode_rig_unloads_loaded_worker_when_it_does_not_fit`.
  - `test_rig_failure_fails_job_with_its_code` (`StageError("rig_failed", ...)` → failed event with that code).
  - `test_cancel_while_painting_skips_rig`.
  - `test_jobs_without_rig_never_touch_rig_runner` (rig fake `runs == []` after a textured and an untextured job).
  - In `test_store.py`: `test_rigging_transitions` (allowed and rejected moves), `test_create_rig_writes_input_and_options`.
  - In `test_quality_presets.py`: `test_shape_face_target` (`target_faces=5000` → 5000; `rig=True, texture=False` → 150000; `rig=True, texture=True` → None; defaults → None).
- [ ] **Step 2: Run** `cd engine && uv run pytest tests/test_scheduler.py tests/test_store.py tests/test_quality_presets.py -q` — new tests FAIL.
- [ ] **Step 3: Implement** the Interfaces block in `store.py`, `hunyuan.py`, then `scheduler.py`.
- [ ] **Step 4: Run** `cd engine && uv run pytest -q` — all pass, including every existing scheduler and swap test unchanged.
- [ ] **Step 5: Commit** `feat(engine): schedule a rig stage with per-card locks`.

### Task 4: API, health, and engine wiring

**Files:**
- Modify: `engine/src/model3d_engine/api/app.py`, `engine/src/model3d_engine/__main__.py`, `engine/src/model3d_engine/config.py`
- Modify: `docs/api.md`, `docs/architecture.md`
- Test: `engine/tests/test_api.py`, `engine/tests/test_main.py`

**Interfaces:**
- Consumes: `Scheduler.submit_rig`, `Scheduler.rig_available`, `JobStore.create_rig`, `RigRunner`, `rig_availability`, `read_memory`.
- Produces:
  - `POST /v1/rig-jobs`, multipart field `model` (GLB), no options: `202` with the `Job`. Errors: `400 invalid_model` (missing part, or not starting with `glTF` magic and version 2), `413 model_too_large` (over `Settings.max_model_bytes = 200 * 1024 * 1024`), `400 rig_unavailable`, `503 engine_not_ready`.
  - `POST /v1/jobs` with `"rig": true` while rigging is unavailable: `400 rig_unavailable` with the availability reason.
  - `glb_has_images(data: bytes) -> bool` in `api/app.py`: parses the GLB JSON chunk and returns whether it has a non-empty `images` array; drives `textured` for rig-only jobs.
  - `build_engine(..., rig_runner: StageRunner | None = None)`: when not given, builds `RigRunner(settings.unirig_dir, settings.hf_home, device, free_mib)` with `device = devices.paint if plan.texture else devices.shape`, `free_mib` reading the rig card's free MiB from `read_memory()` (`total - used`); no rig runner when the mode is `unsupported`. Passes `cards` built from the plan's devices. `health()` adds `"rig"` and `"rig_reason"`.
  - `create_app(..., rig_reason: Callable[[], str | None] = lambda: None)`: used only for the `rig_unavailable` error message. The health body's `rig` and `rig_reason` come from the `health()` callback that `build_engine` builds (`rig = scheduler.rig_available`, `rig_reason = None` when available, else the `rig_availability` reason, or `"Rigging needs a usable GPU"` in `unsupported` mode).
- [ ] **Step 1: Write failing tests** in `test_api.py`: `test_rig_job_accepts_glb_and_reports_rigging`, `test_rig_job_rejects_non_glb` (PNG bytes → 400 `invalid_model`), `test_rig_job_rejects_truncated_glb`, `test_rig_job_rejects_oversized_model` (patch `max_model_bytes` small → 413), `test_rig_option_rejected_when_rig_unavailable` (both endpoints → 400 `rig_unavailable`, message is the reason), `test_glb_has_images` (fixture GLBs with and without images; build them with `trimesh` in the test), `test_health_reports_rig_availability`. In `test_main.py`: `test_build_engine_wires_rig_on_paint_card` (fake inventory, dual mode → rig device equals paint device), `test_build_engine_unsupported_has_no_rig`.
- [ ] **Step 2: Run** `cd engine && uv run pytest tests/test_api.py tests/test_main.py -q` — new tests FAIL.
- [ ] **Step 3: Implement** the Interfaces block.
- [ ] **Step 4: Run** `cd engine && uv run pytest -q` — all pass.
- [ ] **Step 5: Document** in `docs/api.md` the `rig` option, the `rigging` state and `rig` stage, `POST /v1/rig-jobs`, the new error codes, and the health fields; in `docs/architecture.md` a "Rigging (phase 4)" subsection under stage placement with the spike numbers and the card-lock and parking rules.
- [ ] **Step 6: Commit** `feat(engine): accept rigging jobs and report rig availability`.

### Task 5: Real rigging on the GPUs and the setup guide

**Files:**
- Create: `engine/tests/test_rig_gpu.py` (marker `gpu`)
- Modify: `docs/setup.md`, `docs/architecture.md`, `scripts/verify_gpus.py` only if it lists optional components (check first; otherwise leave it)

**Interfaces:**
- Consumes: everything from Tasks 2-4.

- [ ] **Step 1: Write `test_rig_gpu.py`:** `test_penguin_with_skeleton_dual_mode` (the existing penguin fixture through `build_engine` in dual mode with `JobOptions(rig=True)`; assert succeeded, the result GLB has one skin and at least 10 joints, read with `pygltflib` or the GLB JSON chunk, and prints shape/paint/rig seconds and peak VRAM sampled from `nvidia-smi` every 0.5 s); `test_rig_only_on_finished_model` (runs a textured penguin job without `rig`, then submits its result GLB to `POST /v1/rig-jobs`; asserts `textured` stats and a skin); `test_untextured_rig_job_caps_faces` (`texture=False, rig=True` → result faces ≤ 150000).
- [ ] **Step 2: Run** `cd engine && uv run pytest -m gpu tests/test_rig_gpu.py -s` with LM Studio and Ollama unloaded — all pass; record times and peak VRAM.
- [ ] **Step 3: Record** the measured numbers in `docs/architecture.md` (Rigging subsection: whether paint had to be parked on the reference machine).
- [ ] **Step 4: Write the UniRig section in `docs/setup.md`** (optional component): clone at `6793c66` into `vendor/UniRig`; `uv venv --python 3.11 vendor/UniRig/.venv`; the exact install commands from the spec's "What the spike established" (torch cu130, requirements without `flash_attn`, `spconv-cu126`, PyG wheels for `torch-2.9.1+cu130`, `numpy==1.26.4`, then `FLASH_ATTN_CUDA_ARCHS=120 MAX_JOBS=6 NVCC_THREADS=2 CUDA_HOME=/usr/local/cuda-13.1 uv pip install --no-build-isolation flash-attn`, about 18 minutes); weights with `HF_HOME=data/hf` (`skeleton/articulation-xl_quantization_256/model.ckpt`, `skin/articulation-xl/model.ckpt`, 5.8 GB, MIT).
- [ ] **Step 5: Commit** `test(engine): rig real models on the GPUs and document the UniRig setup`.

### Task 6: Client services and generation view model

**Files:**
- Modify: `client/src/model3d_client/services/engine_api.py`, `client/src/model3d_client/services/library.py`, `client/src/model3d_client/viewmodels/generation.py`
- Test: `client/tests/fake_engine.py`, `client/tests/test_engine_api.py`, `client/tests/test_library.py`, `client/tests/test_generation_vm.py`

**Interfaces:**
- Consumes: contracts from Task 1; `POST /v1/rig-jobs` from Task 4.
- Produces:
  - `EngineApi.submit_rig(model: Path, on_done: Callable[[Job | ApiError], None]) -> None` (multipart field `model`, content type `model/gltf-binary`).
  - `Entry.source_entry_id: str | None = None`.
  - `Library.create_derived(source_id: str, suffix: str = " (rigged)") -> Entry`: new running entry copying the source's input image, `name = source.name + suffix`, `options = source.options.model_copy(update={"rig": True})`, `source_entry_id = source_id`.
  - `Library.has_skeleton(entry: Entry) -> bool` is `entry.options.rig`.
  - `GenerationViewModel.rig: bool` (default False), `set_rig(rig: bool)`, `rig_available: bool` (from `supervisor.health.rig`, False without health), `rig_reason: str | None`, `Phase.rigging`, `can_add_skeleton(entry: Entry) -> bool` (engine ready, phase idle, rig available, entry succeeded, not already rigged), `add_skeleton(entry_id: str) -> None` (creates the derived entry and submits the source's `model.glb`; then the same stream, download, and finish path as `generate`).
  - `generate()` sends `rig=self.rig and self.rig_available`.
- [ ] **Step 1: Extend `fake_engine.py`** with `/v1/rig-jobs` and the `rig` health fields, then write failing tests: `test_submit_rig_posts_model_part`, `test_create_derived_copies_input_and_links_source` (original `entry.json` bytes unchanged), `test_generate_sends_rig_option`, `test_rig_stage_maps_to_rigging_phase`, `test_add_skeleton_creates_new_entry_and_downloads_result` (source entry untouched, new entry `succeeded` with `model.glb`), `test_rig_unavailable_disables_rig_and_add_skeleton` (reason exposed), `test_add_skeleton_refused_for_rigged_or_failed_entry`.
- [ ] **Step 2: Run** `cd client && uv run pytest tests/test_engine_api.py tests/test_library.py tests/test_generation_vm.py -q` — new tests FAIL.
- [ ] **Step 3: Implement** the Interfaces block.
- [ ] **Step 4: Run** `cd client && uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat(client): request skeletons for new and existing models`.

### Task 7: Client UI, library rescan, and Spanish

**Files:**
- Modify: `client/src/model3d_client/views/generation_panel.py`, `client/src/model3d_client/views/library_panel.py`, `client/src/model3d_client/views/main_window.py`, `client/src/model3d_client/viewmodels/main.py`, the Spanish catalog under `client/src/model3d_client/i18n/`
- Modify: `docs/architecture.md` (section 5b), `README.md` if it lists app features
- Test: `client/tests/test_main_window.py`, `client/tests/test_main_vm.py`, `client/tests/test_i18n.py`

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - Generation panel: checkbox "With skeleton" bound to `vm.set_rig`; disabled when `not vm.rig_available`, tooltip and hint text show `vm.rig_reason`; progress text for `Phase.rigging` is "Adding skeleton…".
  - `LibraryPanel.add_skeleton_requested = Signal(str)`; context menu action "Add skeleton" enabled when the panel's `can_add_skeleton(entry)` callable (injected from `MainWindow`, backed by `GenerationViewModel.can_add_skeleton`) returns True; entries with a skeleton show "Skeleton" in their row text.
  - `MainViewModel.refresh_entries() -> None` emits `entries_changed`; `MainWindow` calls it on `QApplication.applicationStateChanged` to `ApplicationActive`.
- [ ] **Step 1: Write failing offscreen tests:** `test_rig_checkbox_disabled_with_reason`, `test_rig_checkbox_sends_rig_option`, `test_library_menu_add_skeleton_starts_rig_job`, `test_library_rescans_when_app_becomes_active` (write an entry folder on disk, emit the state change, the list shows it), `test_half_written_entry_is_skipped` (folder with invalid `entry.json` → list still shows the others), `test_spanish_catalog_has_rig_strings` ("With skeleton" → "Con esqueleto", "Add skeleton" → "Agregar esqueleto", "Adding skeleton…" → "Agregando esqueleto…").
- [ ] **Step 2: Run** `cd client && uv run pytest -q` — new tests FAIL.
- [ ] **Step 3: Implement** the Interfaces block; refresh the catalog with `client/scripts/update_translations.sh` and fill the Spanish strings.
- [ ] **Step 4: Run** `cd client && uv run pytest -q` and `cd client && QT_QPA_PLATFORM=wayland uv run pytest -m webengine` — all pass; open a rigged library model in the app and check the viewer shows it.
- [ ] **Step 5: Update docs** (architecture 5b: skeleton option, Add skeleton, focus rescan).
- [ ] **Step 6: Commit** `feat(client): offer skeletons in the generation panel and the library`.

### Task 8: Add-on core (stdlib, tested outside Blender)

**Files:**
- Create: `blender/pyproject.toml` (uv project `model3d-blender`, Python `>=3.11`, dev dependency `pytest` only), `blender/model3d/engine_client.py`, `blender/model3d/library.py`, `blender/model3d/tracker.py`
- Create: `blender/tests/test_engine_client.py`, `blender/tests/test_library.py`, `blender/tests/test_tracker.py`
- Create: `client/tests/test_addon_library_compat.py`

**Interfaces:**
- Produces (no `bpy` import in these three modules):
  - `engine_client.py`: `class EngineError(Exception)` with `.code`, `.message`; `class EngineUnreachable(EngineError)` (code `engine_unreachable`); `class EngineClient(base_url: str = "http://127.0.0.1:8765", timeout: float = 5.0)` with `health() -> dict`, `submit_image(image: Path, options: dict) -> dict`, `submit_rig(model: Path) -> dict`, `job(job_id: str) -> dict`, `cancel(job_id: str) -> dict`, `download(job_id: str, target: Path) -> Path` (writes `.part` then renames). Multipart built by hand with `urllib.request`; API error bodies `{"error": {"code", "message"}}` become `EngineError`.
  - `library.py`: `list_entries(root: Path) -> list[dict]` (readable entries, newest first, skips unreadable ones), `create_entry(root: Path, image: Path, options: dict, name: str | None = None, source_entry_id: str | None = None) -> dict` (same id format and fields as the client's `Library.create`, `outcome="running"`), `update_entry(root: Path, entry: dict) -> None` (atomic replace), `model_path(root: Path, entry_id: str) -> Path`.
  - `tracker.py`: `class JobTracker(client: EngineClient, job_id: str, poll_s: float = 1.0, lost_after: int = 3)` with `poll() -> TrackerStatus`; `TrackerStatus(state: str, stage: str | None, progress: float, error: dict | None, lost: bool)`; `lost` turns True after `lost_after` consecutive `EngineUnreachable`.
  - User-facing message constant: `ENGINE_DOWN_MESSAGE = "Open the Model3D desktop app"`.
- [ ] **Step 1: Write failing tests** against a stdlib `http.server` fake in a thread: `test_health_and_unreachable` (closed port → `EngineUnreachable`), `test_submit_image_multipart_reaches_server` (server checks fields `image` and `options`), `test_submit_rig_sends_model_part`, `test_api_error_becomes_engine_error` (`400 rig_unavailable`), `test_download_writes_atomically`, `test_tracker_reports_progress_then_success`, `test_tracker_marks_lost_after_three_unreachable_polls`, `test_create_entry_matches_client_format`, `test_list_entries_skips_unreadable`. In the client: `test_addon_entry_reads_as_client_entry` (load `blender/model3d/library.py` with `importlib.util.spec_from_file_location`, create an entry, read it with the client's `Library`).
- [ ] **Step 2: Run** `cd blender && uv run pytest -q` and `cd client && uv run pytest tests/test_addon_library_compat.py -q` — FAIL.
- [ ] **Step 3: Implement** the three modules.
- [ ] **Step 4: Run** both commands — all pass.
- [ ] **Step 5: Commit** `feat(blender): add the add-on's engine client, library, and job tracker`.

### Task 9: Blender add-on UI, packaging, and docs

**Files:**
- Create: `blender/model3d/blender_manifest.toml`, `blender/model3d/__init__.py`, `blender/model3d/ui.py`, `blender/model3d/translations.py`
- Create: `blender/tests/blender_smoke.py` (runs inside Blender), `blender/tests/test_blender_smoke.py` (marker `blender`, runs `blender -b --factory-startup --python blender/tests/blender_smoke.py`, skipped when `blender` is not on PATH)
- Modify: `docs/setup.md`, `docs/architecture.md` (new section 5c), `README.md`, `CLAUDE.md` (commands), `AGENTS.md` / `.agents/rules/AGENTS.md` only if a rule changes (it should not)

**Interfaces:**
- Consumes: Task 8 modules.
- Produces:
  - Manifest: `schema_version = "1.0.0"`, `id = "model3d"`, `version = "0.1.0"`, `name = "Model3D"`, `tagline = "Generate and rig 3D models with the local Model3D engine"`, `type = "add-on"`, `blender_version_min = "4.2.0"`, `license = ["SPDX:GPL-3.0-or-later"]`, `permissions = { network = "Talk to the local Model3D engine", files = "Read images and the Model3D library" }`.
  - `AddonPreferences` with `project_dir` (folder holding `data/library`).
  - `Scene.model3d` property group: `image_path`, `quality` (`draft`, `final`, `high`; default `final`), `texture` (True), `rig` (False), plus runtime status fields.
  - Operators: `MODEL3D_OT_generate`, `MODEL3D_OT_cancel`, `MODEL3D_OT_refresh_library`, `MODEL3D_OT_import_entry`, `MODEL3D_OT_add_skeleton`. Network work runs in a `threading.Thread`; a `bpy.app.timers` callback every 1.0 s reads the tracker's latest status and, on success, downloads into the library entry (worker thread) then imports with `bpy.ops.import_scene.gltf` on the main thread and selects the new objects.
  - Panel `MODEL3D_PT_main` in the 3D viewport sidebar, tab "Model3D": image, options, Generate/Cancel, progress (stage and percent), engine message; sub-panel `MODEL3D_PT_library` with a `UIList` of entries and Import / Add skeleton buttons.
  - Before submitting: `health()`; unreachable → status shows `ENGINE_DOWN_MESSAGE`; `ready: false` → "The engine is still loading"; `rig: false` → the Skeleton option is disabled showing `rig_reason`.
  - Generated and rigged models are written to the library with `library.create_entry` / `update_entry` (rigged: `source_entry_id`, name suffix " (rigged)").
  - Spanish through `bpy.app.translations.register(__package__, {"es_ES": {...}})` for every UI string.
- [ ] **Step 1: Write `blender_smoke.py`** asserting, inside `blender -b`: the add-on registers (`bpy.ops.model3d.generate` exists, `bpy.types.Scene.model3d` exists), the panel class is registered, a rigged GLB built inside the test (a cube with an armature of two bones and vertex groups, exported with `bpy.ops.export_scene.gltf` to a temporary folder, then the scene cleared) and imported through the add-on's import helper creates one armature, and unregister leaves no `model3d` operators. Wrap it in `test_blender_smoke.py`.
- [ ] **Step 2: Run** `cd blender && uv run pytest -m blender -q` — FAIL.
- [ ] **Step 3: Implement** the Interfaces block.
- [ ] **Step 4: Run** `cd blender && uv run pytest -q && uv run pytest -m blender -q` — all pass.
- [ ] **Step 5: Manual acceptance in Blender 5.2** with the desktop app open: build with `blender --command extension build --source-dir blender/model3d --output-dir <scratch>`, install from disk, set the project folder; generate the cat image with Skeleton on; the model appears with an armature and textures; pose an arm; close the desktop app mid-job and see "Open the Model3D desktop app"; reopen it and add a skeleton to a library entry from Blender; the new entry appears in the app on focus.
- [ ] **Step 6: Write docs:** `docs/setup.md` (build and install the extension, the project folder preference), `docs/architecture.md` 5c (add-on design: stdlib, threads plus timer, polling, library writes), `README.md` feature list, `CLAUDE.md` commands (`cd blender && uv run pytest`, `-m blender`).
- [ ] **Step 7: Commit** `feat(blender): add the Model3D add-on panel, operators, and packaging`.
