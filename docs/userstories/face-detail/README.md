# Face detail

Status: pending approval.

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

## US-02: Use octree 512 for the high preset

As a user, I want the `high` quality to produce finer geometry, so that faces show more shape detail.

Acceptance criteria:

- US-02 / AC-01: A `high` job calls the shape pipeline with `octree_resolution=512`; `draft` and `final` still use 256 and 384.
- US-02 / AC-02: Existing scheduler, run-mode, and stage tests still pass.
- US-02 / AC-03: The preset comment in `hunyuan.py` states the measured time and VRAM from US-01.

Dependencies: US-01 shows it fits and the user prefers the result. Verification: unit test on the preset mapping plus the engine test suite.

## US-03: Measure more paint views for high (optional)

As a developer, I want to know whether more than 6 paint views fit and help, so that the face texture gets more coverage.

Acceptance criteria:

- US-03 / AC-01: A `high` paint run with 8 views at 768 px is measured for peak VRAM and time on one card.
- US-03 / AC-02: Its texture is compared visually with the 6-view result on the same mesh; results are recorded here, and the default changes only if the user approves.

Dependencies: US-01. Verification: script output and side-by-side review.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `measure_stage_vram.py` at octree 512 | Pending |
| US-01 / AC-02 | Side-by-side GLBs, same seed | Pending |
| US-01 / AC-03 | Results recorded here | Pending |
| US-02 / AC-01 | Unit test on `high` preset octree | Pending |
| US-02 / AC-02 | `uv run pytest` in `engine/` | Pending |
| US-02 / AC-03 | Comment review | Pending |
| US-03 / AC-01 | VRAM script, 8 views | Pending |
| US-03 / AC-02 | Side-by-side textures | Pending |
