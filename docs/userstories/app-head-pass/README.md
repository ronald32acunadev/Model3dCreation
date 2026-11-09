# Head pass in the app

Status: approved 2026-11-08, all stories. Branch: `exp/kit-assembly`.

## Context

The head pass (`docs/userstories/kit-assembly/`, `scripts/kit_assembly/run.py`) gives a sharper face, but only from the command line: the app showed the plain TRELLIS.2 model, and the user kept getting the deformed faces ("sigo obteniendo monstrousidades y no lo que me enseñas en la prueba"). Approval (verbatim): "Entonces implementalo".

## US-01: The head pass returns a rigged model

As a user, I want the head pass's result to keep its skeleton, so that a model I asked to rig stays animatable.

- US-01 / AC-01: Given a rigged job, when `run.py` finishes the head swap, then it rigs the result through the engine's `/v1/rig-jobs` and writes `final.glb`; for a job that was not rigged, `final.glb` is `model.glb`.
- US-01 / AC-02: `run.py --home DIR` reads jobs from `DIR/jobs` and writes runs to `DIR/kit_runs` (the app's data folder may not be the repository's).
- US-01 / AC-03: The last stdout line is `MODEL3D_HEAD_PASS {"model": path, "applied": bool, "reason": str|null}`; on SIGTERM the run stops its steps and any engine it started.

## US-02: A "Detailed face" option

As a user, I want to tick "Detailed face" before generating, so that the model in the viewer and the library already has the sculpted head.

- US-02 / AC-01: With the option on, the job asks for a skeleton; after the model downloads, the panel shows "Sculpting the face" with the current step, and when the pass applies, the library's `model.glb` is the head-pass model and the job's model is kept as `model_original.glb`.
- US-02 / AC-02: When the pass finds no usable head, or fails, the entry keeps the job's model, ends as succeeded, and the panel says why.
- US-02 / AC-03: Cancel during the pass stops it and keeps the job's model.
- US-02 / AC-04: The option is disabled, with the reason, when the engine cannot rig, does not generate with TRELLIS.2, or the head-pass tools are not installed.

## Log

- 2026-11-08: the first real run stopped at the comparison renders (they drew pieces the head swap does not make); the exploded view is skipped without pieces, and a failed review render no longer stops the pass.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-03 | Real run on job 01M6YT0WEV02XG6KCDT2GHRZ6W (second dog) | Passed: `final.glb` has 1 skin with 34 joints over the body and head meshes, rigged in 96 s through `/v1/rig-jobs`; last line `MODEL3D_HEAD_PASS {"applied": true, ...}`. SIGTERM handling covered by code review only, not exercised |
| US-02 / AC-01..AC-03 | `test_generation_vm.py`: `test_detailed_face_replaces_the_model_and_keeps_the_original`, `test_detailed_face_not_applied_keeps_the_job_model_with_a_notice`, `test_failed_head_pass_keeps_the_job_model`, `test_cancel_during_the_head_pass_keeps_the_job_model` | Passed (client suite 290 passed); written after the code, so no RED was observed |
| US-02 / AC-04 | `test_generation_vm.py`: `test_detailed_face_needs_trellis`, `test_detailed_face_needs_the_tools_and_skeletons` | Passed |
| US-02 / AC-01 | One generation in the running app | Pending: the user's latest dog was run by hand and swapped into its library entry (`model_original.glb` kept); a generation with the option ticked in the app has not run yet |
