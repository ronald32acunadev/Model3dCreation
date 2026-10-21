# Engine API Reference (v1)

The engine listens on `http://127.0.0.1:8765/v1` (loopback only, no authentication). Start it with:

```bash
uv run --project engine python -m model3d_engine
```

Both GPU workers load their models when the engine starts (about 50 s); until then job submissions answer `503 engine_not_ready` and `GET /v1/health` reports `"ready": false`. Shared request and event models live in the `model3d_contracts` package (`contracts/`).

## Jobs

A job turns one image into a GLB. States: `queued → shaping → painting → succeeded`, plus `failed` and `cancelled` from any non-final state. A job with `"texture": false` goes `shaping → succeeded`.

### `POST /v1/jobs`

`multipart/form-data` with an `image` file (PNG or JPEG, up to 20 MiB) and an optional `options` field holding JSON:

| Field | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `texture` | bool | `true` | `false` skips the paint stage |
| `quality` | `"draft"` or `"final"` | `"final"` | `final`: 50 steps, octree 384; `draft`: 25 steps, octree 256 |
| `seed` | int or null | `null` | 0 to 2^32-1; `null` draws one, returned in the job |
| `remove_background` | bool | `true` | Skipped when the image already has transparency |
| `target_faces` | int or null | `null` | At least 1000; decimates the shape when it has more faces |

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
| `GET /v1/health` | `200` `{"ready": bool, "gpus": [{"bus_id", "stage", "ready", "memory_used_mib", "memory_total_mib"}]}` | |

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
| `engine_not_ready` | `POST /v1/jobs` while the workers load or restart (503) |
| `job_not_found` | Any job route with an unknown id (404) |
| `job_not_finished` | Result requested before `succeeded` (409) |
| `job_finished` | `DELETE` on a job that already ended (409) |

Job failures (in the job's `error` and the `failed` event): `gpu_out_of_memory`, `worker_crashed` (the stage process died; it restarts on its own), `internal_error`.
