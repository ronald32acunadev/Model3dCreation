# On-demand stages

Status: complete 2026-10-30, except that generating a model from inside the app is left to the user's test (US-03 / AC-01).

Approval (verbatim): "Aprobado, completo".

Request (verbatim): "Creo que el enjine no esta aplicando la logica de la fabrica es decir cuando se ejecuta no deberia cargar ningun modelo".

Earlier design (verbatim, from `docs/userstories/staged-factory/README.md`): "Hagamos eso junto con la carga y liberacion de modelos por etapa asi tendremos una mejor fabrica".

## Facts

- The request is correct. `Scheduler.start` loads the shape worker at startup, and in dual mode the paint worker too (`engine/src/model3d_engine/jobs/scheduler.py:96`). With the app just started in swap mode, the shape card went from 184 MiB to 7230 MiB with no job.
- Models stay loaded while idle. Only swap modes unload one, to make room for the other, and only `RigRunner` already loads nothing between tasks.
- The rule "each stage loads only its model, does its work, and unloads" exists only in `data/spikes/factory/`. Its engine integration was left for later, in that story's US-09.
- `/v1/health` `ready` and `POST /v1/jobs` both depend on the models being loaded: submissions get 503 `engine_not_ready`, "The engine is still loading its models" (`api/app.py:213`). The desktop client's `EngineSupervisor` shows the engine as ready from `health.ready`.
- A missing weight or a placement failure is reported at startup today, through `start_error`.

## Excluded

The factory's mold, parts, sculpting, and assembly stages, whose assembly step is unsolved. The head pipeline in `data/spikes/night/`. How the run mode is chosen. Blender add-on changes beyond what its existing health handling needs.

## Assumptions

- A stage releases its model when no task is waiting for it. Two jobs in a row reuse the loaded model, instead of paying the load twice. To be confirmed by the user's approval.
- The load time is added to the first job's wait. Its size is not measured yet; US-02 measures it.

## US-01: An idle engine holds no model

As a user, I want the engine to load nothing when it starts, so that my GPUs are free until I generate something.

Dependencies: none.

- US-01 / AC-01: Given the engine starts with working GPUs, when `/v1/health` first answers, then `ready` is true, no stage worker process exists, and no card's memory has grown because of the engine.
- US-01 / AC-02: Given an idle engine, when a job is submitted, then it is accepted, and no request gets `engine_not_ready` for models not being loaded.
- US-01 / AC-03: Given the run mode is `unsupported`, when the engine starts, then health and submissions report the mode's error, as they do now.

## US-02: Each stage loads and releases its model

As a user, I want each stage to load its model only for its work, so that the next stage gets the whole card.

Dependencies: US-01.

- US-02 / AC-01: Given a textured job on an idle engine, when it runs, then shape loads, works, and unloads because nothing waits for it, and then paint does the same. After the job, no stage worker process exists.
- US-02 / AC-02: Given a job in shape and another waiting for shape, when the first finishes, then shape is not reloaded for the second.
- US-02 / AC-03: Given a job with a skeleton, when rigging starts, then no shape or paint worker holds the rig card.
- US-02 / AC-04: Given a job waiting for a stage's model, when the job's progress is read, then it says the stage's model is loading.
- US-02 / AC-05: Given a stage whose model fails to load, for example missing weights, when a job needs it, then that job fails with the stage's error code and message, and the engine goes on taking jobs.
- US-02 / AC-06: Given a job waiting for a model to load, when it is cancelled, then it ends as cancelled, and no worker is left loaded if nothing else needs it.
- US-02 / AC-07: Given `MODEL3D_GENERATOR=trellis2`, when a job runs, then the generator also loads for it and is released afterwards.
- US-02 / AC-08: Given a crashed worker with no task waiting, when it dies, then it is not restarted until a task needs it.

## US-03: The app and scripts keep working

As a user, I want the desktop app and the setup wizard to work with an engine that starts empty, so that nothing appears broken.

Dependencies: US-01, US-02.

- US-03 / AC-01: Given the app starts its engine, when health answers, then the app shows it as ready, and generating a model works.
- US-03 / AC-02: Given a fresh install, when the wizard's final engine check runs, then it still reports a usable or an unusable engine correctly. If it relied on models being loaded to find a fault, the check loads them itself.

## Results

Real engine, dual mode, the dog image, textured with a skeleton:

| Moment | Card 0 MiB | Card 1 MiB | Worker processes |
| --- | ---: | ---: | ---: |
| Engine ready, 1.9 s after starting | 912 | 14 | 0 |
| Paint at work | 914 | 9392 | 1 |
| After the job, 254 s in total | 912 | 14 | 0 |

Before this change, the engine's shape card sat at 7230 MiB with no job.

The paint stage waited about 47 s from its start to its first progress with the model on the card, which is about what a load costs. Shape's load was not separated from its work.

TRELLIS.2, the same job, 296 s in total: 912 and 14 MiB before and after, no worker left.

The desktop app restarted on this code: health ready at once, 1083 and 14 MiB. Card 0's memory is the desktop session, not the engine.

Changes beyond the backlog:

- `RigRunner.fits` is gone: rigging now always gets its card alone. In dual mode, textured jobs with a skeleton in a row therefore reload paint after each rig (3 paint loads for 4 such jobs, with fakes).
- The background restart with backoff is gone: after a crash, the next task loads the worker.
- `single_gpu_resident` now uses the swap loop, one model at a time. The independent check found that shape and paint otherwise evicted each other per task (10 loads against 2 for 6 jobs, with fakes). Its name is out of date.

Known and left as they are:

- A cancel while a job waits for its card still loads the model before cancelling. A cancel during a load that then fails ends `failed`.
- In swap mode, paint and rig wait while shape jobs keep arriving, as before.
- `gpus[].ready` is always true on a card with `NoStage` under TRELLIS.2. Nothing reads it.
- The "Loading shape model" message is in the event stream only. The app shows it; the Blender add-on polls and does not.
- `test_high_detail_then_final_on_one_paint_worker` in the GPU tests no longer exercises one worker across jobs.
- The engine's mode still depends on free memory at startup: with 1200 MiB in use on card 0 it picked swap, with 1083 MiB dual.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `test_start_loads_no_model`, `test_idle_engine_reports_ready_with_nothing_loaded`; real run above | Passed |
| US-01 / AC-02 | `test_idle_engine_accepts_a_job` | Passed |
| US-01 / AC-03 | `test_unsupported_mode_serves_health` | Passed |
| US-02 / AC-01 | `test_textured_job_loads_and_releases_each_stage`, `test_swap_textured_job_loads_and_releases_each_stage`; real run above | Passed |
| US-02 / AC-02 | `test_jobs_in_a_row_share_one_load`, `test_single_gpu_resident_uses_the_swap_loop` | Passed |
| US-02 / AC-03 | `test_rig_runs_with_no_worker_on_its_card`; real run above | Passed |
| US-02 / AC-04 | `test_job_progress_says_the_model_is_loading` | Passed for events |
| US-02 / AC-05 | `test_failed_load_fails_the_job_and_the_engine_goes_on`, `test_model_load_failure_fails_the_job_and_health_stays_ready` | Passed |
| US-02 / AC-06 | `test_cancel_while_loading_ends_cancelled_and_releases` | Passed |
| US-02 / AC-07 | Real TRELLIS.2 job above | Passed |
| US-02 / AC-08 | `test_crash_is_not_restarted_until_a_task_needs_it` | Passed |
| US-03 / AC-01 | `client/tests/`, 240 passed; app restarted and health ready; job through the API on the same engine code | Pending for a job started from inside the app: manual, left to the user |
| US-03 / AC-02 | Reading `client/src/model3d_client/setup/recipe.py`: the check is `choose_mode(probe_gpus())`, which never needed loaded models; `client/tests/` passed | Passed |
| Regression: existing pipeline | `engine/.venv/bin/python -m pytest` in `engine/`: 206 passed, 7 deselected; two real jobs above | Passed. `pytest -m gpu` was not run on this code |
| Independent check | Probes in a scratch copy, 60 random seeds per mode | One blocker found and fixed (`single_gpu_resident`). Native review not run: its required file selection format was refused |
