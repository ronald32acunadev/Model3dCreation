# Skin weight smoothing

Status: complete 2026-10-30, not committed. US-01 is implemented in `engine/src/model3d_engine/stages/smooth_skin.py`.

Request (verbatim): "y si me parce que puedes preparar la historia para los pesos rigidos".

Approval (verbatim): "Aprobado, completo".

Reading of the request: this backlog covers the smoothing tested on `N_S3_sides`, which is what the user was offered. Rigid weights, where a part follows one bone only, are a different change and are not in this backlog.

## Evidence

Measured by hand on 2026-10-30. Details of the first row are in `docs/userstories/head-texture/README.md`.

| Model | Faces | Largest weight step across an edge, 99th percentile, before | After 60 passes | Time | Head turned 30° |
| --- | ---: | ---: | ---: | ---: | --- |
| `N_S3_sides`, separate head | 689 957 | 0.074 (head) | 0.013 (head) | about 7 s | Face tore before, whole after |
| Regular engine job `01M64A9HMA8SVK67P69EDNJSR5` | 40 000 | 0.398 | 0.096 | under 2 s | Slight tear at the chin before, gone after |

Comparison for the regular model: `data/spikes/night/models/regular_turn_before_after.png`.

Facts:

- `engine/src/model3d_engine/stages/unirig_job.py` writes the rigged model in its `merge` step, with UniRig's interpreter. That environment has bpy 4.2.0, numpy 1.26.4, and scipy 1.17.1.
- The hand-run script keeps textures and animation-free skeletons through a bpy re-export. File size stayed about the same.
- Coincident vertices split by UV charts must keep equal weights, or the surface opens at chart borders.

Assumptions:

- A fixed 60 passes spreads weights over a wider area on a coarse mesh than on a dense one, because each pass reaches one edge further. Only one coarse model was tried, and only with a head turn. US-01 therefore fixes the spread in model units, not in passes. The value is chosen during implementation against US-01 / AC-03.

Excluded: the head pipeline, separating weapons from the body, the collar stretching with the head, rigid weights, and models already rigged or saved in the library.

## US-01: Rigged models that hold together when they move

As a user, I want the engine to smooth a model's skin weights after rigging, so that the surface does not tear when a joint bends.

Dependencies: none.

- US-01 / AC-01: Given a rigged mesh with stepped weights, when it is smoothed, then the 99th percentile of the weight step across an edge is lower than before, every vertex has at most four influences, and each vertex's weights sum to 1.
- US-01 / AC-02: Given vertices at the same position split by UV charts, when the mesh is smoothed, then their weights stay equal.
- US-01 / AC-03: Given `N_S3_sides` and the regular job above, when each is rigged through `unirig_job.py` and its head is turned 30°, then the face is not torn. When each walks with `animate_rig.py`, then the legs and arms do not drag the torso beyond what they do now, judged by side-by-side renders.
- US-01 / AC-04: Given a rig job, when it finishes, then the result GLB keeps its textures and skeleton, and its vertex and face counts match the `RESULT` line.
- US-01 / AC-05: Given the smoothing step fails, or produces no file, when the job runs, then the job fails with `rig_failed` and the step's name, as the other UniRig steps do. No partial GLB is left as the result.
- US-01 / AC-06: Given the smoothing step, when a rig job reports progress, then progress still only rises and reaches 1.0.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `tests/test_smooth_skin.py::test_steps_become_smaller`, `::test_at_most_four_influences_summing_to_one` | Passed |
| US-01 / AC-02 | `tests/test_smooth_skin.py::test_coincident_copies_keep_equal_weights` | Passed |
| US-01 / AC-03 | Manual: both models through `unirig_job.py`, head turned 30°, `animate_rig.py` walk, renders in `data/spikes/night/models/engine_rig_turn_and_walk.png` | Passed by eye: faces whole on both turns; the regular model's walk looks the same as before at the frame compared (frame 6) |
| US-01 / AC-04 | Manual: the same outputs reopened with trimesh and `animate_rig.py detect` | Passed: textured, biped skeleton; 558 371 vertices and 689 957 faces, and 28 682 and 40 000, matching `RESULT` |
| US-01 / AC-05 | `tests/test_unirig_job.py::test_failed_smoothing_fails_the_job_by_name`, `::test_smoothing_without_a_file_fails_the_job` | Passed |
| US-01 / AC-06 | `tests/test_unirig_job.py::test_progress_only_rises_and_reaches_the_end` | Passed |
| Regression: existing rig stage | `engine/.venv/bin/python -m pytest` in `engine/` | Passed: 198 passed, 7 deselected |
| Regression: real rig job through the API | `pytest -m gpu tests/test_rig_gpu.py::test_penguin_with_skeleton_dual_mode` | Passed: 1 test in 4 min 58 s. The other two GPU rig tests were not run |

## Results

| Model | Weight step, 99th percentile, before | After | Passes |
| --- | ---: | ---: | --- |
| `N_S3_sides`, head | 0.074 | 0.008 | Set by the head's edge length |
| `N_S3_sides`, body | not measured | 0.073 | Set by the body's edge length |
| Regular job, 40 000 faces | 0.398 | 0.205 | Set by the model's edge length |

The spread is 1.5 % of the model's largest extent, which replaces the fixed 60 passes. The regular model's chin tear is gone with this value.

Found by the real run, not by the tests: bpy 4.2 in UniRig's environment segfaults while the interpreter shuts down after a glTF import and export, so the first real jobs failed with `rig_failed` even though the file had been written. `smooth_skin.py` now ends with `os._exit` after flushing. No test outside the `gpu` marker covers this, since it needs UniRig's interpreter.

Not checked: FBX, OBJ, STL, and USDZ export of a smoothed model, and loading it in the app. Rigs in the two manual runs and the GPU test were the only real rigs. Weights on the 166-face neck cover keep large steps (0.27), which has no visible effect since it is flat and dark.
