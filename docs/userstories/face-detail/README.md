# Face detail

Status: complete.

## Context

On 2026-10-29 the user reported that faces on generated models lack detail and chose to try raising the pipeline's resolution first ("option 1") before evaluating other 3D models.

What exists (branch `feature/face-detail`):

- `engine/src/model3d_engine/stages/hunyuan.py` maps quality to `(num_inference_steps, octree_resolution)`: `draft` (25, 256), `final` (50, 384), `high` (50, 384). The `high` preset raises only paint settings today, not shape geometry.
- Paint for `high` renders 6 views at 768 px with both memory savers (peak 7.2 GiB against 9.3 GiB at 512 px, measured earlier) and keeps 150 000 faces.
- Upstream paint already bakes into a 4096 px texture (`texture_size = 1024 * 4`) with a 2048 px render (`render_size = 1024 * 2`), so the bake texture is not the bottleneck; the 768 px per-view generation and the shape grid are.
- `scripts/measure_stage_vram.py` measures load and peak VRAM per stage and preset on one card.

Hypothesis: the face occupies a small share of the octree grid and of each paint view, so raising `octree_resolution` for `high` (and, if VRAM allows, the number of paint views) should add facial geometry. This is unproven for faces; the stories below measure before changing defaults.

Assumptions to confirm when approving:

- The change only affects the `high` preset; `draft` and `final` keep their current values and timings.
- The user judges face quality visually by comparing the same input images before and after; there is no automatic face-quality metric.
- Test images include at least one close, front-facing portrait and one full-body character, supplied by the user or taken from existing jobs.

## US-01: Measure octree 512 on a 12 GB card

As a developer, I want to know the VRAM, time, and face quality of the shape stage at `octree_resolution` 512, so that I can decide whether `high` can use it.

Acceptance criteria:

- US-01 / AC-01: The VRAM script can measure a shape run at a given octree resolution, and running it at 512 on one RTX 5070 prints the shape worker's load and peak VRAM and the run time.
- US-01 / AC-02: The same input images are generated at octree 384 and 512 with the same seed, and both GLBs are kept side by side for visual comparison.
- US-01 / AC-03: The results (VRAM, time, face count, user verdict) are recorded in this file. If the peak does not fit with the run modes' headroom, US-02 is dropped and the finding is recorded.

Dependencies: none. Verification: script output and the user's side-by-side review.

Results (2026-10-29, bus 0000:29:00.0, 11 758 MiB free at idle, seed 1234, `high` preset, `marine` close portrait and `wolf` full body):

| Octree | Shape peak MiB | Shape run s | Raw shape faces (marine / wolf) | Paint peak MiB | Paint run s |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 384 | 8 957 | 90 | 971 244 / 442 812 | 7 678 | 150-158 |
| 512 | 10 111 | 150-154 | 1 731 346 / 787 700 | 7 679-7 697 | 163 |

- 512 fits one card with about 1.6 GiB to spare and adds about 60 s to the shape stage.
- Head close-ups (bpy Workbench renders) show 512 removes the stair-step voxel artifacts on cheeks, forehead, and neck, but adds no new facial features: eyes, mouth, and nose keep the same shape. The textured models look nearly identical.
- The paint stage still decimates every mesh to 150 000 faces (`PAINT_FACES["high"]`), so much of the extra geometry does not reach the final GLB.
- Conclusion: octree is not the main limit on facial detail; the shape model itself is. User verdict: keep 512 for the smoother surface.

## US-02: Use octree 512 for the high preset

As a user, I want the `high` quality to produce finer geometry, so that faces show more shape detail.

Acceptance criteria:

- US-02 / AC-01: A `high` job calls the shape pipeline with `octree_resolution=512`; `draft` and `final` still use 256 and 384.
- US-02 / AC-02: Existing scheduler, run-mode, and stage tests still pass.
- US-02 / AC-03: The preset comment in `hunyuan.py` states the measured time and VRAM from US-01.

Dependencies: US-01 shows it fits and the user prefers the result. Verification: unit test on the preset mapping plus the engine test suite.

Result (2026-10-29): the user chose to keep octree 512 for `high` ("Sí, dejalo en 512"). Because the shape peak rose to 10 111 MiB, `SHAPE_NEED_MIB` in `gpu/modes.py` now uses that worst case, so the run-mode planner never places shape on a card where a `high` job runs out of memory. On the 12 GB target cards this changes no mode; a card with 9.5 to 10.6 GiB free no longer qualifies for shape.

## US-03: Measure more paint views for high (optional)

As a developer, I want to know whether more than 6 paint views fit and help, so that the face texture gets more coverage.

Acceptance criteria:

- US-03 / AC-01: A `high` paint run with 8 views at 768 px is measured for peak VRAM and time on one card.
- US-03 / AC-02: Its texture is compared visually with the 6-view result on the same mesh; results are recorded here, and the default changes only if the user approves.

Dependencies: US-01. Verification: script output and side-by-side review.

Results (2026-10-29, `marine`, octree 512, seed 1234): 8 views raise the paint peak from 7 679 to 8 437 MiB and the run from 163 to 210 s. The face texture is visually unchanged, since upstream only adds low-weight views at ±20° elevation beyond the first six. The default stays at 6 views; the trial change was reverted.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `measure_stage_vram.py` at octree 512 | Passed: peak 10 111 MiB, 150-154 s |
| US-01 / AC-02 | Side-by-side GLBs, same seed | Passed: head renders for marine and wolf |
| US-01 / AC-03 | Results recorded here | Passed |
| US-02 / AC-01 | `test_quality_presets.py::test_every_quality_has_shape_settings` | Passed: failed at 384, passed at 512 |
| US-02 / AC-02 | `engine/.venv/bin/python -m pytest` | Passed: 166 passed, 7 deselected |
| US-02 / AC-03 | Comment review | Passed |
| US-03 / AC-01 | VRAM script, 8 views | Passed: peak 8 437 MiB, 210 s |
| US-03 / AC-02 | Side-by-side textures | Passed: no visible gain, default unchanged |
| Regression: mode planner shape threshold | `test_modes.py::test_thresholds_match_the_measurement` | Passed: failed at 8957, passed at 10111 |
