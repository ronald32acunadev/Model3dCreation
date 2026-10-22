# Desktop App (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A PySide6 desktop app that starts the local engine, turns an image into a 3D model with live progress, shows it in a Three.js viewer, saves the GLB, and keeps a local library, in English and Spanish.

**Architecture:** A new uv project `client/` (package `model3d_client`) in MVVM: `services/` wrap the engine API (Qt network classes on the UI event loop, no threads), the engine process (`QProcess`), the library and settings (JSON files under `data/`); `viewmodels/` are `QObject`s with signals and no widgets; `views/` are widgets with no logic. The viewer is a vendored Three.js page in a `QWebEngineView`, fed through a custom `model3d://` URL scheme. First, the engine's runtime data moves into the git-ignored `data/` folder at the repository root.

**Tech Stack:** Python 3.11+, PySide6 6.12.0 (QtWidgets, QtNetwork, QtWebSockets, QtWebEngine), pydantic models from `model3d_contracts`, Three.js 0.186.1 (vendored), pytest + pytest-qt, Qt Linguist (`pyside6-lupdate`, `pyside6-lrelease`).

**Spec:** `docs/superpowers/specs/2026-10-21-desktop-app-design.md`

## Global Constraints

- Linux only; the app talks to the engine only through the API in `docs/api.md` at `http://127.0.0.1:8765/v1`, and sends no `Origin` header.
- The client never installs PyTorch; it depends on `contracts/` (`model3d-contracts`, editable path source).
- The UI thread never waits on the network, the engine process, or `nvidia-smi`.
- Runtime data lives in `<repo>/data/` (git-ignored): `hf/`, `u2net/`, `jobs/` (engine), `library/`, `settings.json`, `logs/engine.log` (client). `MODEL3D_HOME` overrides the folder for both engine and client.
- No CDN at runtime: Three.js is vendored under `client/src/model3d_client/viewer/vendor/`.
- No mention of third-party programs (LM Studio, Ollama, ...) anywhere in the UI.
- Source strings are English; Spanish lives in `client/src/model3d_client/i18n/model3d_es.ts` (compiled `.qm` committed next to it).
- An engine the app did not start is never stopped by the app.
- Cancel: the app shows "Cancelling…" until the engine's final event; the engine is not changed for faster cancel.
- Repository rules (`AGENTS.md`): English code and docs; Conventional Commits with no AI attribution; source code and tests are synthesized by the local LLM providers first (`pumbastudio`, then `pumbalama`), each request carrying a Library Context Brief; the agent writes it only when both are unavailable.
- Never stage with a blanket `git add`; `.atl/`, `.codegraph/`, `.superpowers/` stay out of commits.

## Review Focus

1. **The app is killed or crashes during a generation.** Next start, the entry must not stay "running" forever: it shows as interrupted. Test: `test_library.py::test_running_entries_become_interrupted_on_load` (Task 3).
2. **The engine dies mid-job (WebSocket closes without a final event).** The generation must end as failed with a clear message, not spin forever. Test: `test_generation_vm.py::test_socket_closed_without_final_event_fails_job` (Task 7).
3. **Another engine is already starting (detection, port not open yet) when the app spawns its own.** The app's process fails to bind; the app must adopt the other engine as external instead of reporting a crash. Test: `test_supervisor.py::test_owned_exit_while_other_engine_answers_adopts_external` (Task 5).
4. **The viewer's URL scheme is asked for a path outside its roots** (`model3d://library/../../etc/passwd`). It must refuse. Test: `test_scheme.py::test_rejects_path_traversal` (Task 8).
5. **A dropped file that is not a PNG/JPEG, or is over 20 MiB.** Generate must stay disabled with the reason shown, without a round trip to the engine. Test: `test_generation_vm.py::test_rejects_unsupported_or_oversized_image` (Task 7).

---

### Task 1: Engine runtime data in `<repo>/data`

**Files:**
- Modify: `engine/src/model3d_engine/config.py:67` (default home)
- Modify: `engine/tests/test_config.py:5-9`
- Modify: `.gitignore`, `docs/setup.md`, `docs/architecture.md`, `CLAUDE.md`

**Interfaces:**
- Produces: `load_settings({}, repo_root=R).home == R / "data"`; `MODEL3D_HOME` still overrides. Hence `R/data/hf`, `R/data/u2net`, `R/data/jobs`.

- [ ] **Step 1: Change the test**

```python
def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s = load_settings({}, repo_root=tmp_path)
    assert s.home == tmp_path / "data"
    # remaining asserts unchanged
```

- [ ] **Step 2: Run it, expect FAIL** — `cd engine && uv run --no-sync pytest tests/test_config.py::test_defaults -v` → FAIL (`.local/share/model3d` != `data`).
- [ ] **Step 3: In `load_settings`, default `home` to `repo_root / "data"`.**
- [ ] **Step 4: Run the engine unit suite** — `cd engine && uv run --no-sync pytest -q` → all pass (116 before this change).
- [ ] **Step 5: Add `data/` to `.gitignore`; update `docs/setup.md` (weights section: `HF_HOME=<repo>/data/hf`), `docs/architecture.md` (runtime data paragraph and repo layout), `CLAUDE.md` ("weights live in `data/hf`").**
- [ ] **Step 6: Move the weights — ask the owner first.** Ask in chat: "Move `~/.local/share/model3d/hf` (18 GB) and `u2net` (168 MB) to `<repo>/data/`? Copy, verify, then delete the old copy." Only on a yes: `mkdir -p data && cp -a ~/.local/share/model3d/hf ~/.local/share/model3d/u2net data/`, compare `du -sb` of both, then remove the originals only after a second yes. Leave `~/.local/share/model3d/jobs` alone.
- [ ] **Step 7: GPU check** (unload any local LLM first) — `cd engine && uv run --no-sync pytest -m gpu -q` → 2 passed, loading weights from `data/hf`.
- [ ] **Step 8: Commit** — `git add engine/src/model3d_engine/config.py engine/tests/test_config.py .gitignore docs/setup.md docs/architecture.md CLAUDE.md` then `git commit -m "feat(engine): keep runtime data in the project's data folder"`.

---

### Task 2: Client project, paths, and settings

**Files:**
- Create: `client/pyproject.toml`, `client/src/model3d_client/__init__.py`, `client/src/model3d_client/paths.py`, `client/src/model3d_client/services/__init__.py`, `client/src/model3d_client/services/settings.py`
- Create: `client/tests/conftest.py`, `client/tests/test_paths.py`, `client/tests/test_settings.py`

**Interfaces:**
- Produces:
  - `paths.REPO_ROOT: Path` = `Path(__file__).resolve().parents[3]`
  - `@dataclass(frozen=True) class AppPaths: repo: Path; data: Path` with properties `library -> data/"library"`, `settings_file -> data/"settings.json"`, `engine_log -> data/"logs"/"engine.log"`, `hf -> data/"hf"`, `engine_python -> repo/"engine/.venv/bin/python"`, `hunyuan -> repo/"vendor/Hunyuan3D-2.1"`, `viewer_dir -> repo/"client/src/model3d_client/viewer"`.
  - `def app_paths(env: Mapping[str, str] | None = None, repo: Path = REPO_ROOT) -> AppPaths` — `data` is `MODEL3D_HOME` (expanded) or `repo/"data"`.
  - `class Settings(BaseModel)`: `language: Literal["en","es"] = "en"`, `default_texture: bool = True`, `default_quality: Quality = Quality.final`, `export_dir: Path = Path.home()`.
  - `def load_settings(path: Path, system_locale: str) -> Settings` and `def save_settings(path: Path, settings: Settings) -> None` (atomic: write `*.tmp`, `os.replace`).

`client/pyproject.toml`: name `model3d-client`, `requires-python = ">=3.11"`, dependencies `PySide6==6.12.0`, `model3d-contracts` (path `../contracts`, editable), `pydantic>=2.10,<3`; dev group `pytest`, `pytest-qt`, `fastapi`, `uvicorn` (fake engine in tests); pytest markers `webengine` (needs a GPU-backed display for QtWebEngine) and `gpu` (needs the real engine and GPUs), `addopts = "-m 'not webengine and not gpu'"`. `conftest.py` sets `QT_QPA_PLATFORM=offscreen` before Qt is imported.

- [ ] **Step 1: Write failing tests**

```python
def test_default_data_is_repo_data(tmp_path):
    p = app_paths({}, repo=tmp_path)
    assert p.data == tmp_path / "data"
    assert p.library == tmp_path / "data/library"
    assert p.engine_python == tmp_path / "engine/.venv/bin/python"

def test_model3d_home_overrides_data(tmp_path):
    assert app_paths({"MODEL3D_HOME": str(tmp_path / "x")}, repo=tmp_path).data == tmp_path / "x"

def test_first_run_language_follows_spanish_locale(tmp_path):
    assert load_settings(tmp_path / "s.json", "es_AR").language == "es"
    assert load_settings(tmp_path / "s.json", "de_DE").language == "en"

def test_settings_round_trip(tmp_path):
    s = Settings(language="es", default_texture=False, default_quality=Quality.draft, export_dir=tmp_path)
    save_settings(tmp_path / "s.json", s)
    assert load_settings(tmp_path / "s.json", "en_US") == s

def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    (tmp_path / "s.json").write_text("{not json")
    assert load_settings(tmp_path / "s.json", "en_US") == Settings()
```

- [ ] **Step 2: Run** `cd client && uv sync && uv run pytest -q` → FAIL (module not found).
- [ ] **Step 3: Implement `paths.py` and `services/settings.py` with the signatures above.**
- [ ] **Step 4: Run** `cd client && uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(client): add client project with paths and settings` (stage `client/pyproject.toml client/uv.lock client/src client/tests`).

---

### Task 3: Library service

**Files:**
- Create: `client/src/model3d_client/services/library.py`, `client/tests/test_library.py`

**Interfaces:**
- Consumes: `AppPaths.library`, `model3d_contracts.jobs.JobOptions`, `JobError`, `model3d_contracts.events.ResultStats`.
- Produces:
  - `class Outcome(str, Enum): running, succeeded, failed, cancelled, interrupted`
  - `class Entry(BaseModel)`: `entry_id: str`, `name: str`, `created_at: datetime`, `options: JobOptions`, `seed: int | None`, `job_id: str | None`, `outcome: Outcome`, `error: JobError | None`, `stats: ResultStats | None`, `input_file: str` (file name inside the entry folder).
  - `class Library`: `__init__(self, root: Path)`; `entries() -> list[Entry]` (newest first); `create(image: Path, options: JobOptions) -> Entry` (copies the image to `<root>/<entry_id>/input<suffix>`, name = image stem, outcome running); `update(entry: Entry) -> None`; `rename(entry_id: str, name: str) -> Entry`; `delete(entry_id: str) -> None`; `folder(entry_id) -> Path`; `model_path(entry_id) -> Path` (`<folder>/model.glb`); `input_path(entry_id) -> Path`.
  - Layout: one folder per entry with `entry.json` (no shared index, so one corrupt entry never hides the others). `entry_id` = UTC `YYYYMMDDTHHMMSS` + `-` + 6 hex chars.

- [ ] **Step 1: Write failing tests**: `test_create_copies_image_and_lists_newest_first`, `test_update_and_rename_persist`, `test_delete_removes_folder`, `test_corrupt_entry_is_skipped` (one `entry.json` with garbage; the other entry still lists), and:

```python
def test_running_entries_become_interrupted_on_load(tmp_path, png):
    lib = Library(tmp_path)
    e = lib.create(png, JobOptions())
    assert Library(tmp_path).entries()[0].outcome == Outcome.interrupted
    assert lib.entries()[0].entry_id == e.entry_id
```

  (`running` → `interrupted` happens in `Library.__init__`, once per app start, and is persisted.)
- [ ] **Step 2: Run** `cd client && uv run pytest tests/test_library.py -q` → FAIL.
- [ ] **Step 3: Implement `library.py`.** Writes are atomic (`tmp` + `os.replace`). `rename` strips whitespace and rejects an empty name with `ValueError`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(client): add local model library`.

---

### Task 4: Engine API client

**Files:**
- Create: `client/src/model3d_client/services/engine_api.py`, `client/tests/fake_engine.py`, `client/tests/test_engine_api.py`

**Interfaces:**
- Consumes: `model3d_contracts` (`Job`, `JobOptions`, `ErrorBody`, `Health`, `parse_event`, `FINAL_EVENT_TYPES`).
- Produces `class EngineApi(QObject)` (`__init__(self, base_url: str = "http://127.0.0.1:8765", parent=None)`), all non-blocking, results by callback on the UI thread:
  - `health(on_done: Callable[[Health | None], None]) -> None` — `None` when the engine does not answer (connection refused, timeout 3 s).
  - `submit(image: Path, options: JobOptions, on_done: Callable[[Job | ApiError], None]) -> None` — `QHttpMultiPart` with `image` and `options` JSON.
  - `cancel(job_id: str, on_done: Callable[[Job | ApiError], None]) -> None`
  - `job(job_id: str, on_done: Callable[[Job | ApiError], None]) -> None`
  - `download_result(job_id: str, target: Path, on_done: Callable[[Path | ApiError], None]) -> None` — writes to `target.with_suffix(".part")`, renames on success.
  - `open_events(job_id: str) -> EventStream`; `class EventStream(QObject)` with signals `event(object)` (a parsed contracts event), `closed()` (socket closed; emitted after a final event too), method `close()`.
  - `@dataclass class ApiError: status: int | None; code: str; message: str` — `code` from `ErrorBody`, or `"engine_unreachable"` when there is no HTTP response.
- `fake_engine.py`: a FastAPI app plus `run_fake_engine(port) -> (url, controller)` in a background uvicorn thread for tests; the controller scripts health, job responses, and the events a job's socket sends (including closing without a final event).

- [ ] **Step 1: Write failing tests** (pytest-qt `qtbot.waitUntil`): `test_health_parses_model`, `test_health_none_when_unreachable` (port with nothing listening), `test_submit_sends_multipart_and_returns_job` (fake asserts the `options` JSON and that no `Origin` header was sent), `test_submit_error_maps_error_body` (503 `engine_not_ready`), `test_events_stream_until_final_then_closed`, `test_download_result_writes_file_atomically`.
- [ ] **Step 2: Run** `cd client && uv run pytest tests/test_engine_api.py -q` → FAIL.
- [ ] **Step 3: Implement with `QNetworkAccessManager` and `QWebSocket`** (no threads, no blocking waits). Keep each `QNetworkReply` referenced until `finished`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(client): add non-blocking engine API client`.

---

### Task 5: Engine supervisor

**Files:**
- Create: `client/src/model3d_client/services/supervisor.py`, `client/tests/test_supervisor.py`

**Interfaces:**
- Consumes: `EngineApi.health`, `AppPaths` (`engine_python`, `data`, `engine_log`, `repo`).
- Produces:
  - `class EngineState(str, Enum): stopped, starting, loading, ready, error, crashed`
  - `class EngineSupervisor(QObject)`: `__init__(self, api: EngineApi, paths: AppPaths, program: list[str] | None = None, poll_ms: int = 2000, start_timeout_s: float = 300)`; signals `state_changed(EngineState)`, `health_changed(object)` (`Health | None`); properties `state`, `health`, `owned: bool`; methods `start()`, `restart()`, `stop(on_stopped: Callable[[], None])`.
  - Default `program`: `[str(paths.engine_python), "-m", "model3d_engine"]`, cwd `paths.repo / "engine"`, env adds `MODEL3D_HOME=<paths.data>`; stdout and stderr append to `paths.engine_log`.
- Behavior:
  - `start()`: health once. Answer → external engine (`owned=False`), state from health. No answer → spawn `QProcess`, `owned=True`, state `starting`.
  - Polling every `poll_ms`: no answer while owned process runs → `starting`; `ready false` and `error null` → `loading`; `ready true` → `ready`; `error` set → `error`. Timer slows to 10 s once `ready`.
  - Owned process exits: if health then answers → adopt it (`owned=False`, Review Focus 3); else `crashed`.
  - Not answering for `start_timeout_s` while `starting` → `error` with the process killed.
  - `stop()`: owned → `terminate()` (SIGTERM, uvicorn shuts the workers down), `kill()` after 15 s, then `on_stopped`; not owned → `on_stopped` at once, process untouched.

- [ ] **Step 1: Write failing tests**, with `program` set to a small Python script in `tests/` that serves `fake_engine` and exits on SIGTERM: `test_external_engine_is_used_and_not_owned`, `test_spawns_when_nothing_answers_and_reaches_ready`, `test_health_error_sets_error_state`, `test_owned_crash_sets_crashed`, `test_stop_terminates_owned_engine`, `test_stop_leaves_external_engine_running`, and:

```python
def test_owned_exit_while_other_engine_answers_adopts_external(qtbot, fake_engine_url, tmp_paths):
    # program exits at once with code 1 (port taken); fake engine on the port answers health
    sup = EngineSupervisor(EngineApi(fake_engine_url), tmp_paths, program=[sys.executable, "-c", "raise SystemExit(1)"], poll_ms=50)
    ...  # fake engine starts answering only after the spawn
    qtbot.waitUntil(lambda: sup.state == EngineState.ready)
    assert sup.owned is False
```

- [ ] **Step 2: Run** `cd client && uv run pytest tests/test_supervisor.py -q` → FAIL.
- [ ] **Step 3: Implement `supervisor.py`.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(client): start, watch, and stop the engine process`.

---

### Task 6: Diagnostics

**Files:**
- Create: `client/src/model3d_client/services/diagnostics.py`, `client/tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `AppPaths`, `Health`, `EngineState`.
- Produces:
  - `class CheckId(str, Enum): gpu, engine_env, hunyuan, weights, engine`
  - `@dataclass class Check: id: CheckId; ok: bool; detail: str` (`detail` is raw data such as a GPU name or the engine's error message; the view adds translated text per `id`).
  - `def run_checks(paths: AppPaths, run: Callable[[list[str]], str] = _run) -> list[Check]` — blocking; the view model runs it on `QThreadPool`. `gpu`: `nvidia-smi -L` lists at least one GPU (missing binary or non-zero exit → not ok); `engine_env`: `paths.engine_python` exists; `hunyuan`: `paths.hunyuan / "hy3dshape"` exists; `weights`: `paths.hf / "hub/models--tencent--Hunyuan3D-2.1"` and `.../models--facebook--dinov2-giant` exist.
  - `def engine_check(state: EngineState, health: Health | None) -> Check` — ok when `ready`; detail is `health.error.message` or `"crashed"`.

- [ ] **Step 1: Write failing tests**: `test_all_checks_pass_with_complete_tree` (tmp tree + fake `run`), `test_missing_weights_reported`, `test_missing_nvidia_smi_reported` (`run` raises `FileNotFoundError`), `test_engine_check_carries_health_error`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(client): diagnose a missing GPU, environment, clone, or weights`.

---

### Task 7: Generation view model

**Files:**
- Create: `client/src/model3d_client/viewmodels/__init__.py`, `client/src/model3d_client/viewmodels/generation.py`, `client/tests/test_generation_vm.py`

**Interfaces:**
- Consumes: `EngineApi` (Task 4), `Library`, `Entry`, `Outcome` (Task 3), `EngineSupervisor.state_changed`, `health_changed` (Task 5), `Settings` (Task 2).
- Produces `class GenerationViewModel(QObject)`:
  - `__init__(self, api: EngineApi, library: Library, supervisor: EngineSupervisor, settings: Settings)`
  - signals: `changed()` (any property below changed), `finished(object)` (the final `Entry`), `library_changed()`.
  - properties: `image: Path | None`, `image_problem: str | None` (`"unsupported_format"`, `"too_large"`), `texture: bool`, `quality: Quality`, `texture_available: bool` (from `health.mode.texture`), `phase: Phase` (`idle, submitting, queued, shaping, painting, cancelling`), `progress: float`, `message: str`, `error: JobError | None`, `can_generate: bool` (engine `ready`, image ok, `phase == idle`), `can_cancel: bool`.
  - methods: `set_image(path: Path)`, `set_texture(bool)`, `set_quality(Quality)`, `generate()`, `cancel()`.
- Flow: `generate()` → `Library.create` (outcome running) → `submit` (`texture` forced false when unavailable) → store `job_id`, `seed` → `open_events`; `progress`/`snapshot` update phase and progress; `completed` → `download_result` to `library.model_path` → entry succeeded with `stats` → `finished`; `failed` → entry failed with the error; `cancelled` → entry cancelled. Socket `closed` with no final event → `job()` once; final state found → handle it like the event; otherwise entry failed with `JobError(code="engine_lost", message=...)`. Submit error → entry failed with the `ApiError` code and message. `cancel()` → `phase = cancelling` at once, then `api.cancel`; a `409 job_finished` is ignored (the final event follows).
- Client-side image check: suffix `.png`, `.jpg`, `.jpeg` (case-insensitive) and size ≤ 20 MiB, else `image_problem` and `can_generate` false.

- [ ] **Step 1: Write failing tests** against the fake engine: `test_generate_happy_path_saves_model_and_entry`, `test_failed_event_records_error`, `test_cancel_shows_cancelling_until_cancelled_event`, `test_texture_disabled_in_shape_only_mode`, `test_submit_503_records_failed_entry`, `test_generate_disabled_until_engine_ready`, and the Review Focus tests:

```python
def test_socket_closed_without_final_event_fails_job(...):
    # fake: events socket sends one progress event then closes; GET /v1/jobs/{id} answers 404
    ...
    assert entry.outcome == Outcome.failed and entry.error.code == "engine_lost"
    assert vm.phase == Phase.idle

def test_rejects_unsupported_or_oversized_image(tmp_path, vm):
    vm.set_image(tmp_path / "a.gif"); assert vm.image_problem == "unsupported_format" and not vm.can_generate
    big = tmp_path / "b.png"; big.write_bytes(b"\0" * (20 * 1024 * 1024 + 1))
    vm.set_image(big); assert vm.image_problem == "too_large" and not vm.can_generate
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(client): drive a generation from image to library entry`.

---

### Task 8: Three.js viewer

**Files:**
- Create: `client/src/model3d_client/viewer/index.html`, `client/src/model3d_client/viewer/viewer.js`, `client/src/model3d_client/viewer/vendor/` (three.module.min.js, `addons/controls/OrbitControls.js`, `addons/loaders/GLTFLoader.js`, `addons/utils/BufferGeometryUtils.js`, `LICENSE`), `client/src/model3d_client/viewer/VENDOR.md`
- Create: `client/src/model3d_client/views/__init__.py`, `client/src/model3d_client/views/scheme.py`, `client/src/model3d_client/views/viewer_widget.py`
- Create: `client/tests/test_scheme.py`, `client/tests/test_viewer_widget.py`

**Interfaces:**
- Produces:
  - `SCHEME = b"model3d"`; `def register_scheme() -> None` (call before `QApplication`); `class AssetSchemeHandler(QWebEngineUrlSchemeHandler)`: `__init__(self, roots: dict[str, Path])` serving `model3d://<root>/<relative path>`; `def resolve(self, url: QUrl) -> Path | None` (pure, testable: `None` for unknown roots, missing files, or a path that resolves outside its root).
  - Roots: `viewer` → `AppPaths.viewer_dir`, `library` → `AppPaths.library`.
  - `class ViewerWidget(QWebEngineView)`: `show_model(path: Path)`, `clear()`, `set_textured(bool)`, `reset_camera()`; signal `load_failed(str)`.
  - JS API on `window.viewer`: `loaded: boolean` (true once the current model is shown), `load(url) -> Promise`, `clear()`, `setTextured(bool)` (swaps in a neutral grey `MeshStandardMaterial`, keeps the originals to restore), `resetCamera()` (frames the model's bounding box). Errors go to `console.error("model3d-viewer-error: " + message)`, which the widget's page turns into `load_failed`.
- `index.html` loads Three.js through an import map pointing at `model3d://viewer/vendor/...`; lighting: hemisphere + directional, neutral background.
- Vendoring: download `three-0.186.1.tgz` from `https://registry.npmjs.org/three/-/three-0.186.1.tgz` into a scratch dir, copy only the files listed above, record version, URL, and sha256 of the tarball in `VENDOR.md`.

- [ ] **Step 1: Write failing tests** in `test_scheme.py` (no display needed): `test_resolves_viewer_file`, `test_unknown_root_is_none`, `test_missing_file_is_none`, and:

```python
def test_rejects_path_traversal(tmp_path):
    (tmp_path / "lib").mkdir(); (tmp_path / "secret").write_text("x")
    h = AssetSchemeHandler({"library": tmp_path / "lib"})
    assert h.resolve(QUrl("model3d://library/../secret")) is None
    assert h.resolve(QUrl("model3d://library/%2e%2e/secret")) is None
```

- [ ] **Step 2: Run** `cd client && uv run pytest tests/test_scheme.py -q` → FAIL.
- [ ] **Step 3: Vendor Three.js; implement `scheme.py`, `viewer.js`, `index.html`, `viewer_widget.py`.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: `webengine` test** `test_viewer_widget.py::test_loads_glb_without_error` (marked `webengine`): commit a small fixture made once with the engine environment, `uv run --project engine --no-sync python -c "import trimesh; trimesh.creation.box().export('client/tests/assets/cube.glb')"`; the test shows it, waits until `window.viewer.loaded` is true (poll with `runJavaScript`), and asserts no `load_failed`. Run `cd client && uv run pytest -m webengine -q` on the desktop session → PASS.
- [ ] **Step 6: Commit** `feat(client): add offline Three.js viewer`.

---

### Task 9: Main window

**Files:**
- Create: `client/src/model3d_client/viewmodels/main.py`, `client/src/model3d_client/views/main_window.py`, `client/src/model3d_client/views/top_bar.py`, `client/src/model3d_client/views/library_panel.py`, `client/src/model3d_client/views/generation_panel.py`, `client/src/model3d_client/views/diagnostics_panel.py`, `client/src/model3d_client/app.py`, `client/src/model3d_client/__main__.py`
- Create: `client/tests/test_main_vm.py`, `client/tests/test_main_window.py`

**Interfaces:**
- Consumes: Tasks 2-8.
- Produces:
  - `class MainViewModel(QObject)`: `__init__(self, paths: AppPaths, api: EngineApi, supervisor: EngineSupervisor, library: Library, generation: GenerationViewModel, settings: Settings)`; signals `selection_changed()`, `diagnostics_changed()`; properties `selected: Entry | None`, `diagnostics: list[Check]`, `show_diagnostics: bool` (state `error` or `crashed`); methods `select(entry_id)`, `rename(entry_id, name)`, `delete(entry_id)` (clears the selection when it was selected), `export(target: Path)` (copies the selected GLB; `ValueError` without one), `refresh_diagnostics()` (runs `run_checks` on `QThreadPool`, adds `engine_check`), `close_request() -> CloseDecision` (`close`, `confirm` when a job runs), `shutdown(on_done)` (stops the supervisor; marks a running entry `interrupted`).
  - `def build_app(argv: list[str], env: Mapping[str, str] | None = None) -> tuple[QApplication, MainWindow]` in `app.py`; `__main__.py` calls it, `supervisor.start()`, and `app.exec()`.
- Layout per spec §5.1 (`QSplitter`: library ~22%, viewer, generation panel ~26%). Top bar: state text per `EngineState`, mode name, `GPU <n> <stage>` per GPU with free VRAM, settings button. Library items: input thumbnail (scaled once, cached in memory), name, date, duration, outcome icon; context menu Rename, Delete (with confirmation). Generation panel: input chooser with drag and drop and preview, "Image" tab plus a disabled "Text (coming later)" tab, texture checkbox (disabled with the reason when unavailable), quality combo, Generate/Cancel button, progress bar with stage label and message, Save GLB (file dialog at `settings.export_dir`). Diagnostics panel replaces the viewer area while `show_diagnostics` and lists each check with a translated sentence per `CheckId` and a "Restart engine" button. Error codes map to translated sentences in one dict in `generation_panel.py` (`invalid_image`, `image_too_large`, `engine_not_ready`, `texture_unavailable`, `gpu_out_of_memory`, `worker_crashed`, `worker_unavailable`, `stage_timeout`, `engine_lost`, `engine_unreachable`, `internal_error`); unknown codes show the engine's message.
- Close: `closeEvent` asks `close_request()`; on `confirm` shows "A model is being generated. Close anyway?"; then `shutdown` and accept once it is done.

- [ ] **Step 1: Write failing tests** (`test_main_vm.py`, fakes for supervisor and api): `test_delete_selected_clears_selection`, `test_export_copies_glb`, `test_close_request_confirms_while_generating`, `test_shutdown_marks_running_entry_interrupted`, `test_diagnostics_shown_on_crash`. `test_main_window.py` (offscreen widgets): `test_generate_button_follows_can_generate`, `test_selecting_entry_shows_model` (viewer replaced by a stub via a constructor argument).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `cd client && uv run pytest -q` → PASS.
- [ ] **Step 5: Manual smoke** with the engine stopped: `uv run --project client python -m model3d_client` → app shows "Starting engine…", then "Loading models…", then ready; generate from `engine/tests/assets/penguin.png`; model shows; close stops the engine (`nvidia-smi` shows the workers gone).
- [ ] **Step 6: Commit** `feat(client): add main window with library, viewer, and generation panel`.

---

### Task 10: Settings and Spanish translation

**Files:**
- Create: `client/src/model3d_client/views/settings_dialog.py`, `client/src/model3d_client/i18n/__init__.py`, `client/src/model3d_client/i18n/model3d_es.ts`, `client/src/model3d_client/i18n/model3d_es.qm`, `client/scripts/update_translations.sh`
- Modify: views from Task 9 (each gets `retranslate()` and handles `QEvent.LanguageChange`), `client/src/model3d_client/app.py`
- Create: `client/tests/test_i18n.py`, `client/tests/test_settings_dialog.py`

**Interfaces:**
- Produces: `def apply_language(app: QApplication, language: Literal["en","es"]) -> None` (removes the current `QTranslator`, installs `model3d_es.qm` for `es`; Qt sends `LanguageChange` to every widget); `class SettingsDialog(QDialog)`: language, default texture, default quality, export folder, and an "Engine" section (mode, GPUs with stage and free VRAM, last 200 lines of `engine.log`, "Restart engine"); OK saves through `save_settings` and calls `apply_language`.
- `update_translations.sh`: `pyside6-lupdate` over `src/model3d_client` into the `.ts`, then `pyside6-lrelease` into the `.qm`.

- [ ] **Step 1: Write failing tests**: `test_switching_to_spanish_retranslates_open_window` (open `MainWindow`, `apply_language(app, "es")`, the Generate button text is "Generar"; back to `en` → "Generate"), `test_every_source_string_is_translated` (parse the `.ts` XML: no `<translation type="unfinished">` and no empty translation), `test_qm_is_up_to_date` (run `pyside6-lrelease` on the `.ts` into `tmp_path`, compare bytes with the committed `.qm`), `test_settings_dialog_saves_and_applies` (change language and export dir, accept, file and app updated).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement; run `client/scripts/update_translations.sh`; translate every string to neutral Spanish in the `.ts`.** **Step 4: Run** `cd client && uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(client): add settings and Spanish translation`.

---

### Task 11: Launcher, docs, and acceptance

**Files:**
- Create: `client/launch.sh`, `client/model3d.desktop`, `client/tests/test_acceptance_gpu.py`
- Modify: `docs/architecture.md` (client section, decisions), `docs/setup.md` (client setup and launcher), `README.md`, `CLAUDE.md` (client commands), `odd/tasks/desktop-app.md` (task log)

**Interfaces:**
- `launch.sh`: resolves its own directory, runs `exec uv run --project "<repo>/client" python -m model3d_client`. `model3d.desktop`: `Type=Application`, `Name=Model3D`, `Exec=` the absolute path of `launch.sh` (written by the setup step in `docs/setup.md`: `sed` the repository path in and copy to `~/.local/share/applications/`), `Terminal=false`, `Categories=Graphics;3DGraphics;`.

- [ ] **Step 1: GPU acceptance test** `test_acceptance_gpu.py` (marked `gpu`): with no engine on 8765, build the app, `supervisor.start()`, wait until `ready` (timeout 300 s), generate from `engine/tests/assets/penguin.png` with texture, wait for `finished` (timeout 600 s), assert the entry succeeded, `model.glb` exists and starts with `b"glTF"`, a new `Library` lists it, then `shutdown` and assert port 8765 no longer answers.
- [ ] **Step 2: Run** `cd client && uv run pytest -m gpu -q` (local LLMs unloaded) → PASS. Also `cd client && uv run pytest -m webengine -q` → PASS.
- [ ] **Step 3: Manual acceptance against spec §9**: external engine left running after close; Cancel shows "Cancelling…" then a cancelled entry; Save GLB opens in Blender 5.2 with textures (`mcp__Blender__*` or by hand); with `data/hf` renamed temporarily, diagnostics say the weights are missing; switching to Español changes every visible string. Record results in `odd/tasks/desktop-app.md`.
- [ ] **Step 4: Write the launcher, the `.desktop` entry, and the docs.** `docs/setup.md` gains "Desktop app": `cd client && uv sync`, launcher install commands.
- [ ] **Step 5: Run all suites** — `cd client && uv run pytest -q`, `cd engine && uv run --no-sync pytest -q`, `cd contracts && uv run pytest -q` → all PASS.
- [ ] **Step 6: Commit** `feat(client): add launcher and document the desktop app`.
