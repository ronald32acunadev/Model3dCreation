# Engine Backend (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local FastAPI engine that accepts an image, runs Hunyuan3D 2.1 shape on GPU0 and paint on GPU1 as a two-stage pipeline, and serves progress over WebSocket and the textured GLB over REST.

**Architecture:** The API process (FastAPI + asyncio scheduler) never imports torch. Each GPU stage runs in its own spawned worker process that loads its model once and takes tasks over a pipe; this isolates CUDA contexts, the `bpy`-before-`pymeshlab` import rule, and the teardown hang. The scheduler moves jobs from a shape queue to a paint queue, so job N paints while job N+1 builds its shape.

**Tech Stack:** Python 3.11, uv, FastAPI 0.115.12, uvicorn 0.34.3, Pydantic 2.10.6, python-multipart, PyTorch 2.9.1+cu130, Hunyuan3D 2.1 (vendored), pytest with the `anyio` marker.

**Spec:** `docs/architecture.md` (sections 1, 2, 4, 5 and "Runtime rules for the paint stage"). Phase source: `Fases/fase-2-motor-backend-dual-gpu.md`.

## Global Constraints

- Bind to `127.0.0.1` only, port `8765`, base path `/v1`; no authentication.
- Job states: `queued → shaping → painting → succeeded`; `failed` and `cancelled` reachable from any non-final state; shape-only jobs go `shaping → succeeded`.
- Error body is always `{"error": {"code": "<code>", "message": "<text>"}}` with codes `invalid_image`, `invalid_options`, `image_too_large`, `engine_not_ready` (spec), plus `job_not_found`, `job_not_finished`, `job_finished` (this plan's calls). Job failure codes: `gpu_out_of_memory` (spec), `worker_crashed`, `internal_error`.
- Timestamps ISO 8601 UTC with a `Z` suffix; `progress` is 0.0 to 1.0 within the current stage.
- Placement by PCI bus ID, never CUDA index: shape and paint helpers (DINOv2, VAE decode) on `0000:25:00.0`, paint diffusion on `0000:29:00.0`; `CUDA_DEVICE_ORDER=PCI_BUS_ID` in every worker.
- Runtime data under `MODEL3D_HOME` (default `~/.local/share/model3d`): `hf/` (set as `HF_HOME`), `jobs/<job_id>/`.
- Paint worker: import `bpy` before anything imports `pymeshlab`; call `torchvision_fix.apply_fix()` before importing the paint pipeline; exit with `os._exit`.
- The API process and unit tests never import torch, diffusers, or Hunyuan3D; GPU tests carry `@pytest.mark.gpu`.
- English for all code, identifiers, comments, docs, and commit messages; Conventional Commits with no AI attribution.

## Review Focus

1. A client opens the WebSocket after the job already finished: it must get a `snapshot` with the final state, then the final event, then a close, not hang.
2. Two jobs submitted back to back: the second must start its shape while the first paints, and each job's events must carry only its own `job_id`.
3. A worker process dies mid-task (CUDA OOM, segfault): the job goes to `failed` with code `worker_crashed`, the worker restarts, and the next job still runs.
4. `DELETE` on a job that is already `succeeded`, `failed`, or `cancelled`: returns `409 job_finished` and leaves the state unchanged.
5. An upload that is a valid PNG with an alpha channel versus an RGB JPEG: both are accepted; background removal runs only when `remove_background` is true and the image has no transparency.

---

## File Structure

```text
contracts/
  pyproject.toml                      # package model3d-contracts, pydantic only
  src/model3d_contracts/__init__.py
  src/model3d_contracts/jobs.py       # JobOptions, JobState, Job, ErrorBody
  src/model3d_contracts/events.py     # Snapshot/Progress/Completed/Failed/Cancelled events
  tests/test_contracts.py
engine/
  pyproject.toml                      # becomes a package; adds contracts + web deps
  src/model3d_engine/__init__.py
  src/model3d_engine/__main__.py      # `python -m model3d_engine`
  src/model3d_engine/config.py        # Settings from environment
  src/model3d_engine/gpu/topology.py  # moved pure helpers from scripts/verify_gpus.py + Placement
  src/model3d_engine/jobs/store.py    # JobRecord, JobStore, new_job_id, state machine
  src/model3d_engine/jobs/events.py   # EventBus (per-job fan-out with snapshot)
  src/model3d_engine/jobs/scheduler.py
  src/model3d_engine/stages/protocol.py   # StageRunner protocol, StageTask, StageResult, StageError
  src/model3d_engine/stages/process.py    # ProcessStageRunner + worker_main (spawned process)
  src/model3d_engine/stages/hunyuan.py    # GPU code: ShapeStage, PaintStage (worker side only)
  src/model3d_engine/api/app.py       # create_app(); REST + WebSocket routes
  tests/...                           # one test file per module above
scripts/verify_gpus.py                # imports helpers from model3d_engine.gpu.topology
docs/api.md                           # contract reference
```

---

### Task 1: Shared contracts package

**Files:**
- Create: `contracts/pyproject.toml`, `contracts/src/model3d_contracts/{__init__,jobs,events}.py`, `contracts/tests/test_contracts.py`

**Interfaces:**
- Produces:
  - `JobState(str, Enum)`: `queued, shaping, painting, succeeded, failed, cancelled`; `FINAL_STATES: frozenset[JobState]` = succeeded, failed, cancelled.
  - `Quality(str, Enum)`: `draft, final`.
  - `JobOptions(BaseModel)`: `texture: bool = True`, `quality: Quality = Quality.final`, `seed: int | None = None` (0 ≤ seed < 2**32), `remove_background: bool = True`, `target_faces: int | None = None` (≥ 1000, message "target_faces must be at least 1000"); `model_config = ConfigDict(extra="forbid")`.
  - `JobLinks(BaseModel)`: `self`, `events`, `result` (str).
  - `JobError(BaseModel)`: `code: str`, `message: str`. `ErrorBody(BaseModel)`: `error: JobError`.
  - `Job(BaseModel)`: `job_id: str`, `state: JobState`, `stage: Literal["shape","paint"] | None`, `progress: float`, `queue_position: int | None`, `seed: int`, `created_at: datetime`, `finished_at: datetime | None`, `error: JobError | None`, `links: JobLinks`.
  - Events, all with `job_id: str`, `ts: datetime`, discriminated by `type`: `SnapshotEvent(type="snapshot", state, stage, progress)`, `ProgressEvent(type="progress", stage, progress, message: str)`, `CompletedEvent(type="completed", result_url: str, stats: ResultStats)`, `FailedEvent(type="failed", error: JobError)`, `CancelledEvent(type="cancelled")`; `ResultStats`: `vertices: int`, `faces: int`, `textured: bool`, `duration_s: float`; `Event = Annotated[Union[...], Field(discriminator="type")]`; `parse_event(data: str | bytes) -> Event` via `TypeAdapter`.
  - Datetimes serialize as `"2026-10-20T19:05:00Z"` (UTC, `Z` suffix): use a shared `UtcDatetime = Annotated[datetime, PlainSerializer(...)]`.

- [ ] **Step 1: Write failing tests** in `contracts/tests/test_contracts.py`:
  - `test_options_defaults`: `JobOptions()` equals `texture=True, quality="final", seed=None, remove_background=True, target_faces=None`.
  - `test_options_rejects_small_target_faces`: `JobOptions(target_faces=999)` raises `ValidationError` whose message contains `target_faces must be at least 1000`.
  - `test_options_rejects_unknown_field`: `JobOptions.model_validate({"colour": 1})` raises.
  - `test_event_round_trip`: for each of the five event types built with `ts=datetime(2026,10,20,19,5,tzinfo=UTC)`, `parse_event(e.model_dump_json()) == e` and the JSON contains `"ts":"2026-10-20T19:05:00Z"`.
  - `test_final_states`: `FINAL_STATES == {succeeded, failed, cancelled}`.
- [ ] **Step 2:** `cd contracts && uv run pytest -q` → FAIL (module missing).
- [ ] **Step 3:** Implement. `pyproject.toml`: name `model3d-contracts`, `requires-python = ">=3.11"`, dependency `pydantic==2.10.6`, hatchling with `packages = ["src/model3d_contracts"]`, dev group `pytest`.
- [ ] **Step 4:** `cd contracts && uv run pytest -q` → all pass.
- [ ] **Step 5:** Commit `feat(contracts): add shared job and event models`.

### Task 2: Engine package, settings, and GPU topology

**Files:**
- Modify: `engine/pyproject.toml` (package build, new deps), `scripts/verify_gpus.py`, `engine/tests/test_verify_gpus.py`
- Create: `engine/src/model3d_engine/{__init__,config}.py`, `engine/src/model3d_engine/gpu/{__init__,topology}.py`, `engine/tests/test_config.py`, `engine/tests/test_topology.py`

**Interfaces:**
- Consumes: pure helpers now in `scripts/verify_gpus.py` (`normalize_bus_id`, `bus_id_from_props`, `GpuLink`, `parse_nvidia_smi`, `NVIDIA_SMI_COMMAND`).
- Produces:
  - `model3d_engine.gpu.topology`: the moved helpers (same names and behavior) plus `@dataclass(frozen=True) class Placement: shape: str; paint: str; helpers: str` (torch device strings like `"cuda:0"`) and `resolve_placement(links: list[GpuLink], shape_bus: str, paint_bus: str) -> Placement`: with PCI bus ordering, a GPU's CUDA index is its position in the ascending sorted bus IDs; `helpers` equals `shape`; raises `ValueError("GPU 0000:xx:00.0 not found")` for a missing bus and `ValueError("shape and paint must be different GPUs")` when equal.
  - `model3d_engine.config.Settings` (frozen dataclass): `home: Path`, `host: str = "127.0.0.1"`, `port: int = 8765`, `shape_bus: str = "0000:25:00.0"`, `paint_bus: str = "0000:29:00.0"`, `hunyuan_dir: Path`, `max_image_bytes: int = 20 * 1024 * 1024`; `jobs_dir` and `hf_home` properties (`home / "jobs"`, `home / "hf"`). `load_settings(env: Mapping[str, str] | None = None, repo_root: Path | None = None) -> Settings` reads `MODEL3D_HOME`, `MODEL3D_HOST`, `MODEL3D_PORT`, `MODEL3D_SHAPE_BUS`, `MODEL3D_PAINT_BUS`, `MODEL3D_HUNYUAN_DIR` (default `<repo_root>/vendor/Hunyuan3D-2.1`), normalizing bus IDs with `normalize_bus_id`.

- [ ] **Step 1: Write failing tests:**
  - `test_config.py::test_defaults` (empty env, `repo_root=tmp_path`, `HOME` monkeypatched): `home == HOME/.local/share/model3d`, `port == 8765`, `hunyuan_dir == tmp_path/"vendor/Hunyuan3D-2.1"`, `jobs_dir == home/"jobs"`.
  - `test_config.py::test_env_overrides`: `MODEL3D_SHAPE_BUS="00000000:2A:00.0"` → `shape_bus == "0000:2a:00.0"`; `MODEL3D_PORT="9000"` → `9000`.
  - `test_topology.py::test_resolve_placement_real_machine`: on the verbatim nvidia-smi output from `test_verify_gpus.py`, `resolve_placement(links, "0000:25:00.0", "0000:29:00.0") == Placement("cuda:0", "cuda:1", "cuda:0")`, and swapping the buses gives `Placement("cuda:1", "cuda:0", "cuda:1")`.
  - `test_topology.py::test_resolve_placement_errors`: missing bus and equal buses raise with the messages above.
  - Advisory findings from the phase 1 review (lineage `review-38b7a0ed30ea86e6`), fixed here because this task touches the script: `test_verify_gpus.py::test_device_props_error_is_a_failed_check` (monkeypatch `get_device_properties` to raise → a `fail` check, no traceback); `test_verify_gpus.py::test_wrong_gpu_count_keeps_links` (3 links → `nvidia-smi` check fails with `expected 2 GPUs, found 3` and `device order` still compares the parsed links); `test_verify_gpus.py::test_nvcc_major_mismatch_fails` (fake `nvcc` printing `release 12.8` with torch CUDA `13.0` → `nvcc` check fails naming both). Unknown PCIe generations raise `ValueError` in `GpuLink.bandwidth_gbps`; drop the unused `argv` parameter of `main`.
- [ ] **Step 2:** `cd engine && uv run pytest -q tests/test_config.py tests/test_topology.py` → FAIL.
- [ ] **Step 3:** Move the pure helpers into `topology.py`; `scripts/verify_gpus.py` imports them from `model3d_engine.gpu.topology` and keeps `plan_placement`, `Check`, `run_checks`, `format_report`, `main`. Change `[tool.uv] package = false` to a hatchling build with `packages = ["src/model3d_engine"]`; add dependencies `model3d-contracts` (source `{ path = "../contracts", editable = true }`), `fastapi==0.115.12`, `uvicorn==0.34.3`, `python-multipart`, `pydantic==2.10.6`; dev group adds `httpx`.
- [ ] **Step 4:** `cd engine && uv run pytest -q` → all pass, including the unchanged `test_verify_gpus.py`; then `uv run --project engine python scripts/verify_gpus.py` from the repo root still ends with `RESULT: OK`.
- [ ] **Step 5:** Commit `refactor(engine): package the engine and share GPU topology helpers`.

### Task 3: Job store and state machine

**Files:**
- Create: `engine/src/model3d_engine/jobs/{__init__,store}.py`, `engine/tests/test_store.py`

**Interfaces:**
- Consumes: `JobState`, `FINAL_STATES`, `JobOptions`, `JobError`, `Job`, `JobLinks` (Task 1); `Settings.jobs_dir` (Task 2).
- Produces:
  - `new_job_id(now_ms: int | None = None, rand: bytes | None = None) -> str`: 26-char Crockford base32 ULID (48-bit ms timestamp + 80 random bits), lexicographically sortable by time.
  - `class InvalidTransition(Exception)`.
  - `@dataclass class JobRecord`: `job_id`, `options: JobOptions`, `seed: int`, `state: JobState`, `stage: str | None`, `progress: float`, `created_at: datetime`, `finished_at: datetime | None`, `error: JobError | None`, `cancel_requested: bool`, `dir: Path`; methods `transition(new: JobState, *, error: JobError | None = None) -> None` (raises `InvalidTransition` per the Global Constraints graph; sets `finished_at` on final states; sets `stage` to `"shape"`/`"paint"`/`None` and resets `progress` to 0.0 on entering shaping/painting), `to_job(queue_position: int | None) -> Job` (links `/v1/jobs/{id}`, `/v1/jobs/{id}/events`, `/v1/jobs/{id}/result`).
  - `class JobStore`: `__init__(jobs_dir: Path)`, `create(options: JobOptions, image_bytes: bytes, image_suffix: str) -> JobRecord` (makes `jobs_dir/<id>/`, writes `input<suffix>`, draws a random seed when `options.seed is None`), `get(job_id: str) -> JobRecord | None`, `persist(record: JobRecord) -> None` (writes `job.json` from `to_job(None)`).

- [ ] **Step 1: Write failing tests:** `test_new_job_id_is_sortable_ulid` (26 chars from `0123456789ABCDEFGHJKMNPQRSTVWXYZ`; ids at `now_ms=1` and `now_ms=2` sort in order); `test_happy_path_transitions` (queued→shaping→painting→succeeded sets `finished_at`, stage goes shape→paint→None); `test_shape_only_transition` (shaping→succeeded allowed); `test_invalid_transitions` (queued→painting, succeeded→failed, cancelled→shaping raise `InvalidTransition`); `test_create_writes_input_and_seed` (file `input.png` holds the bytes; seed in `[0, 2**32)` when not given, equals the given seed otherwise); `test_persist_writes_job_json` (`json.loads` of `job.json` has `"state": "queued"`).
- [ ] **Step 2:** run `uv run pytest -q tests/test_store.py` → FAIL.
- [ ] **Step 3:** Implement as specified.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(engine): add job store and state machine`.

### Task 4: Per-job event bus

**Files:**
- Create: `engine/src/model3d_engine/jobs/events.py`, `engine/tests/test_events.py`

**Interfaces:**
- Consumes: event models (Task 1), `JobRecord` (Task 3).
- Produces: `class EventBus`: `publish(event: Event) -> None` (sync, safe to call from the event loop thread); `async subscribe(record: JobRecord) -> AsyncIterator[Event]`: yields a `SnapshotEvent` built from `record` first, then every later event for that job; if the record is already final it yields the snapshot and the stored final event, then stops; stops after yielding a final event (`completed`, `failed`, `cancelled`). The bus keeps each job's final event for late subscribers.

- [ ] **Step 1: Write failing tests** (`@pytest.mark.anyio`): `test_subscriber_gets_snapshot_then_events`; `test_late_subscriber_after_completion_gets_snapshot_and_final_then_stops` (Review Focus 1); `test_events_are_isolated_per_job` (two jobs, each subscriber sees only its `job_id`; Review Focus 2); `test_two_subscribers_same_job_both_receive`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement with one `asyncio.Queue` per subscriber. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(engine): add per-job event bus`.

### Task 5: Stage protocol and two-stage scheduler

**Files:**
- Create: `engine/src/model3d_engine/stages/{__init__,protocol}.py`, `engine/src/model3d_engine/jobs/scheduler.py`, `engine/tests/fakes.py`, `engine/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `JobStore`, `JobRecord`, `EventBus`, events.
- Produces:
  - `protocol.py`: `@dataclass(frozen=True) class StageTask: job_id: str; job_dir: Path; options: JobOptions; seed: int`; `@dataclass(frozen=True) class StageResult: output: Path; vertices: int; faces: int`; `class StageError(Exception)` with `code: str`, `message: str`; `class StageRunner(Protocol)`: `ready: bool`; `async start() -> None`; `async run(task: StageTask, on_progress: Callable[[float, str], None]) -> StageResult`; `async stop() -> None`.
  - `class Scheduler`: `__init__(store: JobStore, bus: EventBus, shape: StageRunner, paint: StageRunner)`; `async start()` / `async stop()` (start both runners and two loops); `submit(record: JobRecord) -> None`; `cancel(job_id: str) -> bool` (False when already final); `queue_position(job_id: str) -> int | None` (0-based index in the shape queue, None once shaping started); `ready: bool` (both runners ready).
  - Behavior: shape loop takes a job, skips it as `cancelled` if `cancel_requested`, transitions to shaping, runs shape (output `shape.glb`), then either succeeds (texture false: result `model.glb` is the shape output, renamed) or hands the job to the paint queue; paint loop does the same for painting with output `model.glb`. Every progress callback publishes a `ProgressEvent`; success publishes `CompletedEvent` with `result_url` and `stats`; a `StageError` publishes `FailedEvent` with its code; any other exception fails the job with code `internal_error`. Cancellation takes effect at the next stage boundary and publishes `CancelledEvent`. The record is persisted after every transition.
  - `tests/fakes.py`: `FakeRunner(name, delay=0.0, fail_with: StageError | None = None)` that writes a tiny GLB with `trimesh.creation.box()` to `task.job_dir / f"{name}.glb"`, reports progress 0.5 and 1.0, and records start/end times.

- [ ] **Step 1: Write failing tests** (`@pytest.mark.anyio`, fake runners):
  - `test_textured_job_runs_both_stages` (final state `succeeded`, `model.glb` exists, event types in order `progress… completed`, stats `textured=True`).
  - `test_shape_only_job_skips_paint`.
  - `test_jobs_pipeline_across_stages` (Review Focus 2): two textured jobs with `delay=0.2`; job 2's shape start is before job 1's paint end.
  - `test_stage_error_fails_job` (`StageError("gpu_out_of_memory", ...)` in paint → state `failed`, `error.code == "gpu_out_of_memory"`, next job still succeeds).
  - `test_cancel_before_paint` (cancel while shaping → state `cancelled` after shape, paint never runs, `CancelledEvent` published).
  - `test_cancel_finished_job_returns_false` (Review Focus 4, scheduler side).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(engine): add two-stage job scheduler`.

### Task 6: REST API

**Files:**
- Create: `engine/src/model3d_engine/api/{__init__,app}.py`, `engine/tests/test_api.py`

**Interfaces:**
- Consumes: `Settings`, `JobStore`, `EventBus`, `Scheduler`, contracts.
- Produces: `create_app(settings: Settings, scheduler: Scheduler, store: JobStore, bus: EventBus, health: Callable[[], dict]) -> FastAPI`. Lifespan calls `scheduler.start()`/`stop()`. Routes:
  - `POST /v1/jobs` (multipart `image` file + `options` form field holding JSON, default `{}`): 413 `image_too_large` above `settings.max_image_bytes`; 400 `invalid_image` unless Pillow opens it as PNG or JPEG; 400 `invalid_options` with the first Pydantic error message; 503 `engine_not_ready` when `not scheduler.ready`; else 202 with `record.to_job(queue_position)`.
  - `GET /v1/jobs/{id}` → `Job`; 404 `job_not_found`.
  - `GET /v1/jobs/{id}/result` → `FileResponse(model.glb, media_type="model/gltf-binary")`; 409 `job_not_finished` unless `succeeded`; 404 unknown.
  - `DELETE /v1/jobs/{id}` → 202 with the job; 409 `job_finished` when final (Review Focus 4); 404 unknown.
  - `GET /v1/health` → `{"ready": bool, "gpus": [...]}` from the injected `health()`.
  - All errors through one exception handler that emits `ErrorBody`.

- [ ] **Step 1: Write failing tests** with `fastapi.testclient.TestClient` and the fake runners: `test_submit_returns_202_and_job` (state `queued`, links match the job id); `test_png_with_alpha_and_rgb_jpeg_are_accepted` (Review Focus 5, API side); `test_rejects_non_image` (400 `invalid_image`); `test_rejects_oversized_image` (settings with `max_image_bytes=10` → 413); `test_rejects_bad_options` (`{"target_faces": 5}` → 400, message contains `target_faces must be at least 1000`); `test_not_ready_returns_503`; `test_result_before_finish_is_409_then_glb_after` (poll `GET` until `succeeded`, then result bytes start with `b"glTF"`); `test_unknown_job_is_404`; `test_delete_finished_job_is_409`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(engine): add REST API for jobs`.

### Task 7: WebSocket events

**Files:**
- Modify: `engine/src/model3d_engine/api/app.py`
- Create: `engine/tests/test_websocket.py`

**Interfaces:**
- Consumes: `EventBus.subscribe` (Task 4).
- Produces: `WS /v1/jobs/{id}/events`: sends each event as JSON text (`model_dump_json()`), closes with code 1000 after a final event; unknown job closes with code 4404 before sending anything.

- [ ] **Step 1: Write failing tests:** `test_ws_streams_snapshot_progress_completed` (types in order start with `snapshot`, end with `completed`, then the socket closes); `test_ws_after_completion_gets_snapshot_and_final` (Review Focus 1); `test_ws_unknown_job_closes_4404`. Parse every frame with `parse_event`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(engine): stream job events over WebSocket`.

### Task 8: Process-isolated stage runner

**Files:**
- Create: `engine/src/model3d_engine/stages/process.py`, `engine/tests/test_process_runner.py`, `engine/tests/worker_stubs.py`

**Interfaces:**
- Consumes: `StageRunner`, `StageTask`, `StageResult`, `StageError` (Task 5).
- Produces:
  - `ProcessStageRunner(factory: str, kwargs: dict, env: dict[str, str], start_timeout_s: float = 600)` implementing `StageRunner`. `factory` is a dotted path `"module:callable"` resolved **inside** the worker; it returns an object with `run(task: StageTask, progress: Callable[[float, str], None]) -> StageResult`.
  - Worker side: `worker_main(factory: str, kwargs: dict, env: dict, conn: Connection) -> None`, started with the `spawn` context; applies `env` to `os.environ` before importing the factory, sends `("ready",)` after construction, then loops on `("run", task)` messages, sending `("progress", value, message)`, `("done", result)` or `("error", code, message)`; on `("stop",)` or a closed pipe it calls `os._exit(0)`.
  - Parent side: `run()` awaits messages without blocking the loop (`loop.run_in_executor` around `conn.poll`/`recv`); if the process dies mid-task, raises `StageError("worker_crashed", "<stage> worker exited with code N")`, then restarts the worker in the background, setting `ready = False` until the new `ready` arrives (Review Focus 3).
  - `worker_stubs.py`: `EchoStage` (writes a box GLB, reports progress 0.5) and `CrashStage` (calls `os._exit(3)` inside `run`) for tests.

- [ ] **Step 1: Write failing tests** (`@pytest.mark.anyio`): `test_runner_round_trip` (EchoStage: result path exists, progress callback got 0.5); `test_worker_env_is_applied` (stage returns `os.environ["MODEL3D_TEST_FLAG"]` in its message); `test_crash_fails_task_and_restarts` (CrashStage → `StageError` with code `worker_crashed`; after restart, `ready` becomes True again within 30 s).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(engine): run GPU stages in isolated worker processes`.

### Task 9: Hunyuan3D shape and paint stages

**Files:**
- Create: `engine/src/model3d_engine/stages/hunyuan.py`, `engine/tests/test_hunyuan_gpu.py`, `engine/tests/assets/penguin.png` (copy of upstream `assets/demo.png`)

**Interfaces:**
- Consumes: `StageTask`, `StageResult`, `StageError`, `Placement` device strings.
- Produces (worker side only; imported only inside worker processes):
  - `make_shape_stage(hunyuan_dir: str, device: str) -> ShapeStage`; `ShapeStage.run(task, progress) -> StageResult`: loads `input.*`, converts to RGBA, runs `BackgroundRemover` only when `options.remove_background` and the image has no alpha below 255 (Review Focus 5); calls `Hunyuan3DDiTFlowMatchingPipeline` with `generator=torch.Generator(device).manual_seed(task.seed)`, presets `final = (num_inference_steps=50, octree_resolution=384)` (upstream defaults, measured 77 s) and `draft = (25, 256)` (this plan's call; record measured time in the log), `callback` reporting `step / num_inference_steps`; decimates to `options.target_faces` when set; exports `shape.glb`.
  - `make_paint_stage(hunyuan_dir: str, device: str, helper_device: str) -> PaintStage`: module top imports `bpy` first, then torch; applies `torchvision_fix.apply_fix()`; builds `Hunyuan3DPaintConfig(max_num_view=6, resolution=512)` with `device`; moves DINOv2 to `helper_device` with its output returned to `device`; enables VAE slicing and wraps `vae.decode` to run on `helper_device`; verifies the attention patch is present in both the vendor file and the loaded snapshot copy (raise `RuntimeError` naming `docs/setup.md` step 5 otherwise). `PaintStage.run` reports coarse progress (`0.1 remesh`, `0.3 multiview`, `0.9 bake`, `1.0 export`), writes `model.glb`, maps `torch.OutOfMemoryError` to `StageError("gpu_out_of_memory", ...)`.
  - Both stages get their environment from the runner: `CUDA_DEVICE_ORDER=PCI_BUS_ID`, `HF_HOME=<home>/hf`, `HF_HUB_OFFLINE=1`, `PYTORCH_ALLOC_CONF=expandable_segments:True`.

- [ ] **Step 1: Write the failing GPU test** `@pytest.mark.gpu test_penguin_end_to_end`: build a `Scheduler` with two `ProcessStageRunner`s pointing at these factories (placement from `resolve_placement`), submit the penguin with defaults, wait up to 15 min; assert state `succeeded`, `model.glb` loads with `trimesh` as a scene with at least one textured material, `stats.faces > 1000`, and print the stage durations.
- [ ] **Step 2:** `cd engine && uv run pytest -m gpu tests/test_hunyuan_gpu.py -s` → FAIL (module missing).
- [ ] **Step 3:** Implement, porting the measured spike (shape on GPU0, DINOv2 and VAE decode on GPU0, diffusion on GPU1).
- [ ] **Step 4:** Unload any LM Studio/Ollama model, run the same command → PASS; record durations and peak VRAM in the task log.
- [ ] **Step 5:** Commit `feat(engine): run Hunyuan3D shape and paint stages on both GPUs`.

### Task 10: Entrypoint, health, and docs

**Files:**
- Create: `engine/src/model3d_engine/__main__.py`, `engine/tests/test_main.py`, `docs/api.md`
- Modify: `CLAUDE.md` (Commands), `docs/setup.md` (run section)

**Interfaces:**
- Consumes: everything above.
- Produces: `build_engine(settings: Settings) -> FastAPI` wiring store, bus, runners (factories from Task 9, placement from `resolve_placement` on live `nvidia-smi`), scheduler, and a `health()` that reports per GPU `bus_id`, `stage` (`"shape"`, `"paint"`), `ready`, `memory_used_mib`, `memory_total_mib` (parsed from nvidia-smi). `main() -> None` runs `uvicorn.run(app, host=settings.host, port=settings.port)`; refuses a non-loopback host with exit code 2 and the message `MODEL3D_HOST must be a loopback address`.

- [ ] **Step 1: Write failing tests:** `test_main_rejects_non_loopback_host` (env `MODEL3D_HOST=0.0.0.0`, `main()` raises `SystemExit(2)`); `test_build_engine_with_fake_factories_serves_health` (monkeypatch the factory paths to `worker_stubs:EchoStage`; `GET /v1/health` lists two GPUs with stages `shape` and `paint`).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; write `docs/api.md` from `docs/architecture.md` section 5 plus the `job_not_found`, `job_finished`, `worker_crashed`, `gpu_out_of_memory`, `internal_error` codes; add `uv run --project engine python -m model3d_engine` to `CLAUDE.md` and `docs/setup.md`. **Step 4:** run the full suite `cd engine && uv run pytest -q` → PASS; then start the engine and run one real job with `curl -F image=@engine/tests/assets/penguin.png http://127.0.0.1:8765/v1/jobs`, confirm `model.glb` downloads.
- [ ] **Step 5:** Commit `feat(engine): add engine entrypoint and API reference`.
