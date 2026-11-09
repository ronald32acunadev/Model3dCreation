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

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-03 | Real run on a rigged job; rigged output opens in Blender with an armature | Pending |
| US-02 / AC-01..AC-03 | `client/tests/test_generation_vm.py` with a fake head pass | Pending |
| US-02 / AC-04 | `client/tests/test_main_window.py`: option disabled with reason | Pending |
| US-02 / AC-01 | One generation in the running app | Pending |
