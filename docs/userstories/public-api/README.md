# Public API

Status: approved 2026-11-08 ("Approve, all stories"), execution mode: all stories. US-01, US-02 and US-06 complete (US-06 / AC-03's "the app shows it" is checked in the running app with US-07); US-03 implemented, its real GPU run pending; US-04 next. Branch: `feat/public-api-voice` (from `exp/kit-assembly`, which holds the head pass).

Implementer route: LM Studio and Ollama had no model loaded on 2026-11-08, so US-01 and US-02 went to a lightweight hosted implementer (haiku subagent), per `AGENTS.md`. From US-06 on, pumbastudio (LM Studio, `qwen/qwen3.8-27b`) is available and implements first.

## Context

Request (verbatim): "necesito que el app que creamos funciones mediante un api publico tanto para utilizar desde otro proyecto que se va ejecutar en un puerto dispinible quiero utilziar FASTApi , quiero agregar en este API las funciones que hemos creado en la aplicacion".

Decisions in chat (2026-11-08):

- Consumers run on the same machine and on the LAN ("1,2"); not on the internet.
- Every app function goes in the API ("Todas por favor"): generate, rig, Detailed face, export formats, walk animation, library.
- Approach A: the engine itself grows these functions, and the desktop app uses the same endpoints; no separate gateway.
- No `0.0.0.0`: "quiero la ip de red algo formato 192.168.90.10". The engine always listens on `127.0.0.1` (the desktop app and the Blender add-on use it) and also on the configured LAN address.

Existing facts: the engine is already FastAPI (`engine/src/model3d_engine/api/app.py`) with `/v1/jobs`, `/v1/rig-jobs`, `/v1/health` and WebSocket events, guarded by `LoopbackGuard` (loopback `Host` only). The head pass (`scripts/kit_assembly/run.py`), export (`client/.../services/convert_model.py`) and walk (`client/.../services/animate_rig.py`) already run with `engine/.venv/bin/python`, launched by the client.

Assumptions (to confirm at approval):

- API keys come from `MODEL3D_API_KEYS` (comma-separated) and travel only in `Authorization: Bearer <key>`, also on the WebSocket; never in the URL.
- The library stays at `$MODEL3D_HOME/library` with its current layout; the API reads it and can save a finished job into it.

Out of scope: internet exposure, HTTPS, user accounts, rate limits.

## Revision R1/R2 (approved 2026-11-08, "Approve R1 and R2")

Found while preparing US-03..US-05; US-01 and US-02 are unaffected.

- R1 (US-03 / AC-02): the head-pass tools never use the GPU themselves; their GPU work is sub-jobs sent to the engine (`/v1/jobs`, `/v1/rig-jobs`). Holding a card while they run would deadlock, so the pass runs without a card and its sub-jobs follow the one-task-per-card rule like any job. The job reports a new state `detailing` (stage `face`) between `rigging` and `succeeded`.
- R2 (US-04, US-05): in the app, export and walk act on library entries, not jobs (walk makes a new `"<name> (walk)"` entry). The endpoints move to the library so the app can use them in US-07: `POST /v1/library/{id}/exports` + `GET /v1/library/{id}/exports/{format}`, `GET /v1/library/{id}/body` (detected body type) and `POST /v1/library/{id}/walk` (answers the new entry). US-06 comes before US-04 and US-05; the library entry model moves to `contracts/`.

## US-01: The engine listens on the LAN address and a free port

As a developer of another project, I want the engine reachable at my machine's LAN address on an available port, so that a program on my network can use it.

- US-01 / AC-01: With `MODEL3D_LAN_HOST=192.168.90.10`, the engine answers `GET /v1/health` on both `127.0.0.1:<port>` and `192.168.90.10:<port>`.
- US-01 / AC-02: Without `MODEL3D_LAN_HOST`, it listens only on `127.0.0.1`, as today.
- US-01 / AC-03: When the LAN address is not assigned to any local interface, the engine exits with an error naming the address and listens nowhere.
- US-01 / AC-04: With `MODEL3D_PORT=auto`, it binds a free port; with a fixed port that is busy, it exits with an error naming the port.
- US-01 / AC-05: After binding, it writes `$MODEL3D_HOME/engine.json` with `{"port", "hosts"}` and logs the URLs. The desktop app starts its engine on the port it polls (its `EngineApi` URL), whatever `MODEL3D_PORT` the shell sets; `engine.json` is for other programs. (Changed during implementation: reading the file in the app would move the app and the Blender add-on off their fixed `127.0.0.1:8765`.)
- US-01 / AC-06: The `Host` check accepts the configured LAN address as well as loopback names.

## US-02: LAN clients need an API key

As the machine's owner, I want LAN clients to present a key, so that nobody else on the network can use my GPUs.

- US-02 / AC-01: A request reaching the LAN address without a valid `Authorization: Bearer <key>` gets 401 with `{"error": {"code": "unauthorized", ...}}`; a WebSocket is closed before accept.
- US-02 / AC-02: Requests on `127.0.0.1` need no key (the desktop app and Blender add-on work unchanged).
- US-02 / AC-03: With `MODEL3D_LAN_HOST` set and `MODEL3D_API_KEYS` empty, the engine refuses to start and says why.
- US-02 / AC-04: The existing Origin (CSRF) check still applies to both addresses.

## US-03: Detailed face through the API

As an API client, I want to ask for the Detailed face option when I submit an image, so that I get the sculpted head without the desktop app.

- US-03 / AC-01: `POST /v1/jobs` accepts `detailed_face: true` (requires `rig: true`, the TRELLIS.2 generator, and the head-pass tools); without `rig` it answers 400 `invalid_options` ("detailed_face needs rig: true"), and otherwise 400 `detailed_face_unavailable` with the reason. (Changed during implementation from "422" to the engine's existing 400 convention for invalid options.)
- US-03 / AC-02: After rigging, the job enters state `detailing` (stage `face`); the scheduler runs the head pass without holding a card, its GPU work runs as ordinary sub-jobs under the one-task-per-card rule, and its steps are published as progress events.
- US-03 / AC-03: When the pass applies, `/v1/jobs/{id}/result` is the head-pass model and `/v1/jobs/{id}/result?variant=original` the plain one; when it does not apply, the job succeeds with the plain model and a reason in the record.
- US-03 / AC-04: Cancel during the pass stops it and keeps the plain model.
- US-03 / AC-05: `/v1/health` reports whether Detailed face is available and, if not, why.

## US-04: Export a library model to another format

As an API client, I want to convert a library model to another format, so that I can use it in tools that do not read GLB.

- US-04 / AC-01: `POST /v1/library/{id}/exports` with `{"format": <one of the app's formats>}` starts an export and answers 202; an unknown format answers 422 listing the valid ones; an unknown entry answers 404.
- US-04 / AC-02: When it finishes, `GET /v1/library/{id}/exports/{format}` returns the file (a zip when the format writes several files, as in the app); before that it answers 409 with the export's state.
- US-04 / AC-03: Exporting an entry that has no finished model answers 409.

## US-05: Add a walk animation to a library model

As an API client, I want a rigged model with a walk cycle, so that I can animate it without Blender.

- US-05 / AC-01: `GET /v1/library/{id}/body` returns the detected body type the app shows; an entry without a skeleton answers 409.
- US-05 / AC-02: `POST /v1/library/{id}/walk` with `{"body": "biped" | "quadruped"}` on a rigged entry starts the walk and answers 202 with the new `"<name> (walk)"` entry, which ends succeeded with the animated model; the source entry is unchanged.
- US-05 / AC-03: When the rig has no legs the tool can detect, the new entry ends failed with the same reason the app shows.

## US-06: Library through the API

As an API client, I want to list, save and download library models, so that the other project sees the same models as the app.

- US-06 / AC-01: `GET /v1/library` lists entries (id, name, created, files); `GET /v1/library/{id}` returns one; an unknown id answers 404.
- US-06 / AC-02: `GET /v1/library/{id}/files/{name}` returns a file of the entry; a name outside the entry (for example `../x`) answers 404.
- US-06 / AC-03: `POST /v1/library` with `{"job_id", "name"}` saves a finished job the way the app does, and the app shows it.
- US-06 / AC-04: The library entry model lives in `contracts/`, used by both the engine and the app.

## US-07: The desktop app uses the engine's endpoints

As a user, I want the app to do Detailed face, export and walk through the engine, so that the app and the API cannot drift apart.

- US-07 / AC-01: The app no longer launches `run.py`, `convert_model.py` or `animate_rig.py` itself; it calls US-03..US-06 and shows the same progress, errors and results as before.
- US-07 / AC-02: The existing client tests for these flows pass against the fake engine, updated to the new endpoints.

## US-08: API documentation

As a developer of another project, I want accurate documentation, so that I can integrate without reading the engine's code.

- US-08 / AC-01: `/docs` and `/openapi.json` describe every endpoint, request and error code (served on loopback; on the LAN they need the key).
- US-08 / AC-02: `docs/api.md` explains the variables, the key, and a curl example per endpoint, and each example was run against a real engine.

## Dependencies

US-01 → US-02 → US-03 and US-06 → (US-04, US-05) → US-07 → US-08.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-06 | `engine/tests/test_network.py` (5 tests), `test_config.py` (port and LAN host cases), `test_main.py::test_main_serves_on_loopback_and_writes_engine_info`, `test_main_exits_when_lan_address_is_missing`, `test_api.py::test_lan_host_header_is_allowed`; engine suite 221 passed | Passed |
| US-01 / AC-01, AC-03..AC-05 | Real engine run, `MODEL3D_LAN_HOST=192.168.90.10 MODEL3D_PORT=auto` | Passed: `engine.json` `{"port": 41353, "hosts": ["127.0.0.1", "192.168.90.10"]}`, `/v1/health` 200 on both addresses, two `Listening on` lines; `MODEL3D_LAN_HOST=192.0.2.1` → `192.0.2.1 is not an address of this machine`, exit 2; busy 8765 → `Port 8765 is already in use on 127.0.0.1`, exit 2 |
| US-01 / AC-05 (app) | `client/tests/test_supervisor.py::test_engine_listens_on_the_polled_port` (RED without the change, then GREEN); client suite 291 passed | Passed |
| US-02 / AC-01..AC-04 | `engine/tests/test_auth.py` (9 tests: no key, wrong key, no `Bearer` prefix, valid key, loopback and `::1` without key, WebSocket 4401, foreign Origin and Host with a valid key), `test_config.py` (keys split), `test_main.py::test_main_refuses_lan_without_keys`; RED observed (missing field), engine suite 233 passed | Passed |
| US-02 / AC-01..AC-03 | Real engine run on `192.168.90.10` with `MODEL3D_API_KEYS=test-key-1` | Passed: no keys → `MODEL3D_LAN_HOST needs MODEL3D_API_KEYS: ...`, exit 2; loopback without key 200; LAN without key 401 with the exact body; wrong key 401; valid key 200; LAN with a spoofed `Host: 127.0.0.1` and no key 401 |
| US-01, US-02 (security, risk item 2) | Independent verifier (sonnet): read uvicorn 0.34.3 source, live probes on 192.168.90.10 (spoofed Host, lowercase `bearer`, empty key, `X-Forwarded-For`, `/docs`, WebSocket) | Passed: no blockers; `scope["server"]` comes from the accepted socket's `getsockname()` and proxy headers never rewrite it. Advisory: IPv6 LAN hosts untested (the spec names IPv4) |
| US-03 / AC-01..AC-05 | `contracts/tests/test_contracts.py`, `engine/tests/test_store.py`, `test_head_pass.py` (stub script: steps, applied, skipped, exit 1, cancel), `test_scheduler.py` (applied, skipped, failed, cancel during detailing, and a head pass that waits for a second job, swap and dual, 10 s timeout: no deadlock), `test_api.py`, `test_main.py`; RED observed for each; engine 270, contracts 20, client 291, blender 29, system Python 290 passed. Code from pumbastudio, reviewed | Passed |
| US-03 / AC-01..AC-04 | One real run with Detailed face (GPU) | Pending: LM Studio's model fills both GPUs. Advisory: the job's stats are the rigged model's, not the head-pass model's |
| US-04 / AC-01..AC-03 | `engine/tests/test_exports.py` with a fake converter; one real FBX export of a library entry | Pending |
| US-05 / AC-01..AC-03 | `engine/tests/test_walk.py` with a fake animator; one real walk on a rigged library entry | Pending |
| US-06 / AC-01..AC-04 | `engine/tests/test_library.py` (9 tests, implementation from pumbastudio, tests corrected for `write_text` and `model_copy`), `test_scheduler.py::test_succeeded_job_writes_its_stats` (RED: no `stats.json`, then GREEN); engine 243, client 291, contracts 17, system Python 290 passed | Passed. A `..%2F` path decodes to `../`, leaves `/v1/library` and gets FastAPI's generic 404 (`{"detail"}`), not the engine's error body; no file is served |
| US-06 / AC-01, AC-02 | Real engine on a copy of library entry `20261109T005950-d46c5d` | Passed: listed with `input.jpeg`, `model.glb`, `model_original.glb`; `model.glb` download identical; `entry.json` → 404 `file_not_found`; unknown job → 404 `job_not_found`. Saving a real job waits for a GPU run |
| US-07 / AC-01..AC-02 | client `uv run pytest`; one generation with Detailed face, one export and one walk in the running app | Pending |
| US-08 / AC-01..AC-02 | `test_api.py` checks the OpenAPI paths; curl examples run against the real engine | Pending |
