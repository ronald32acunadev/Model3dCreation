# Engine API Reference (v1)

The engine listens on `http://127.0.0.1:8765/v1` (loopback only, no authentication). Start it with:

```bash
uv run --project engine python -m model3d_engine
```

At startup the engine detects the NVIDIA GPUs and picks a run mode (see [Run modes](#run-modes)); `GET /v1/health` reports it. Detection runs before the port opens and takes a few seconds (up to about two minutes if the driver hangs, after which the engine starts in `unsupported` mode). The port answers as soon as detection ends. The GPU workers load their models in the background (about 50 s); until they are ready, `GET /v1/health` reports `"ready": false` and job submissions answer `503 engine_not_ready`. If a worker fails to load, the engine does not retry: health reports the failure in `error` and submissions answer `503 engine_not_ready` with the cause, until the engine is fixed and restarted. A worker that crashes restarts on its own (retrying after 0, 5, 30, and 120 s); jobs that reach it meanwhile wait for the restart. A task that runs longer than `MODEL3D_STAGE_TIMEOUT_S` (default 900 s; measured runs take 60-80 s) fails with `stage_timeout`, and its stuck worker is killed and restarted the same way. Shared request and event models live in the `model3d_contracts` package (`contracts/`).

### Run modes

| `mode.name` | When | Behavior |
| :--- | :--- | :--- |
| `dual_gpu` | Two GPUs each fit one stage | Paint on the card with the most free VRAM, shape on the other; job N paints while job N+1 shapes |
| `single_gpu_resident` | One GPU fits both stages at once (about 17.5 GiB free) | Both workers stay loaded; jobs run one after another |
| `single_gpu_swap` | One GPU fits one stage at a time (about 10.4 GiB free) | Only one worker is loaded; the loaded stage finishes its queue, then the card swaps (about 20-30 s per swap). While a swap runs, top-level `ready` stays true and each GPU's `ready` is false. If a stage cannot load, its waiting jobs fail with `worker_unavailable` and the other stage is loaded back; if that fails too, health `error` becomes `worker_unavailable` and submissions answer 503 until the engine is restarted |
| `shape_only` | One GPU fits shape but not paint | Untextured models only; a textured request answers `400 texture_unavailable` |
| `unsupported` | No usable NVIDIA GPU, or too little free VRAM | Nothing loads; health `error` is `mode_unavailable` with the reason, and submissions answer `503 engine_not_ready` |

The engine measures free VRAM once, before loading anything, so close other GPU apps (LM Studio, Ollama, browsers) before starting it for the best mode. `MODEL3D_MODE` forces a mode (any name above except `unsupported`), and `MODEL3D_SHAPE_BUS` with `MODEL3D_PAINT_BUS` (set both or neither) force the cards; forced settings skip the free-VRAM check, and a mode the hardware cannot provide at all is reported as `mode_unavailable`.

### Browser access

Every request must carry a loopback `Host` header (`127.0.0.1`, `localhost`, `[::1]`, with any port), which blocks DNS rebinding. A `POST` or `DELETE` that carries an `Origin` header is refused unless that origin is listed in `MODEL3D_ALLOWED_ORIGINS` (comma-separated, for example `http://localhost:5173`; empty by default), so a web page cannot queue or cancel jobs. Clients that send no `Origin`, such as curl and the desktop app, are not affected. Refused HTTP requests answer `403 forbidden_origin`; a refused WebSocket is closed with code `4403` before it opens.

## Jobs

A job turns one image into a GLB. States: `queued → shaping → painting → succeeded`, plus `failed` and `cancelled` from any non-final state. A job with `"texture": false` goes `shaping → succeeded`.

### `POST /v1/jobs`

`multipart/form-data` with an `image` file (PNG or JPEG, up to 20 MiB) and an optional `options` field holding JSON:

| Field | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `texture` | bool | `true` | `false` skips the paint stage |
| `quality` | `"draft"`, `"final"`, or `"high"` | `"final"` | Shape: `draft` 25 steps, octree 256; `final` and `high` 50 steps, octree 384. Paint keeps 40000 faces and paints 512 px views (`draft`, `final`); `high` keeps 150000 faces and paints 768 px views, running the guidance branches one at a time with the paint models offloaded to RAM (about 1.5 minutes more; see `docs/spikes/paint-high-detail.md`) |
| `seed` | int or null | `null` | 0 to 2^32-1; `null` draws one, returned in the job |
| `remove_background` | bool | `true` | Skipped when the image already has transparency |
| `target_faces` | int or null | `null` | At least 1000; decimates the shape when it has more faces, and is the face count a textured model keeps (overrides the quality level) |

```bash
curl -F image=@penguin.png -F 'options={"quality": "draft"}' http://127.0.0.1:8765/v1/jobs
```

Answers `202` with the job:

```json
{
  "job_id": "01M5FFK5G23E0PG2A9Q9EPVABW",
  "state": "queued",
  "stage": null,
  "progress": 0.0,
  "queue_position": 0,
  "seed": 3141592653,
  "created_at": "2026-10-21T09:27:00Z",
  "finished_at": null,
  "error": null,
  "links": {
    "self": "/v1/jobs/01M5FFK5G23E0PG2A9Q9EPVABW",
    "events": "/v1/jobs/01M5FFK5G23E0PG2A9Q9EPVABW/events",
    "result": "/v1/jobs/01M5FFK5G23E0PG2A9Q9EPVABW/result"
  }
}
```

`queue_position` counts jobs waiting for the shape stage (0 = next); it is `null` once the job started. `progress` is 0.0 to 1.0 within the current `stage`.

### Other endpoints

| Method and path | Success | Errors |
| :--- | :--- | :--- |
| `GET /v1/jobs/{id}` | `200` job | `404 job_not_found` |
| `GET /v1/jobs/{id}/result` | `200` `model/gltf-binary` | `404 job_not_found`, `409 job_not_finished` |
| `DELETE /v1/jobs/{id}` | `202` job; a queued job is cancelled at once, a running one at its next stage boundary | `404 job_not_found`, `409 job_finished` |
| `GET /v1/health` | `200` `{"ready": bool, "mode": {"name", "texture", "reason"}, "gpus": [{"bus_id", "stage", "ready", "memory_used_mib", "memory_total_mib", "memory_free_mib"}], "error": null or {"code", "message"}}`. `stage` is `"shape"`, `"paint"`, `"shape+paint"`, or `null` for an unused GPU; `memory_free_mib` is the free VRAM when the engine started. `error` is set when no mode is usable (`mode_unavailable`) or the workers failed to load (`worker_start_failed`, or `internal_error`). The model is `model3d_contracts.health.Health` | |

## Events: `WS /v1/jobs/{id}/events`

The server sends JSON text frames and closes with code `1000` after a final event. An unknown job is closed with code `4404` before any frame. The first frame is always a `snapshot`, so a client that connects late, even after the job ended, sees the current state and then the final event.

| `type` | Fields besides `job_id` and `ts` |
| :--- | :--- |
| `snapshot` | `state`, `stage`, `progress` |
| `progress` | `stage` (`shape` or `paint`), `progress`, `message` |
| `completed` | `result_url`, `stats` (`vertices`, `faces`, `textured`, `duration_s`) |
| `failed` | `error` (`code`, `message`) |
| `cancelled` | none |

## Errors

Every error response has the same body:

```json
{ "error": { "code": "invalid_options", "message": "target_faces must be at least 1000" } }
```

| Code | Where |
| :--- | :--- |
| `invalid_image` | `POST /v1/jobs`: missing file, or not a PNG or JPEG (400) |
| `image_too_large` | `POST /v1/jobs`: over 20 MiB (413) |
| `invalid_options` | `POST /v1/jobs`: options are not valid JSON or break a rule (400) |
| `texture_unavailable` | `POST /v1/jobs` with `"texture": true` (the default) in `shape_only` mode (400) |
| `engine_not_ready` | `POST /v1/jobs` while the workers load or restart, or after they failed to load or no mode was usable; the message then names the cause (503) |
| `forbidden_origin` | Any route: the `Host` is not loopback, or a `POST` or `DELETE` carries an `Origin` not in `MODEL3D_ALLOWED_ORIGINS` (403) |
| `job_not_found` | Any job route with an unknown id (404) |
| `job_not_finished` | Result requested before `succeeded` (409) |
| `job_finished` | `DELETE` on a job that already ended (409) |

Job failures (in the job's `error` and the `failed` event): `gpu_out_of_memory`, `worker_crashed` (the stage process died while running this job; it restarts on its own), `worker_unavailable` (the stage could not be restarted; see the engine log), `stage_timeout` (the stage ran longer than `MODEL3D_STAGE_TIMEOUT_S`; its worker is killed and restarts on its own), `invalid_image` (the stored input image is missing), `internal_error`.
