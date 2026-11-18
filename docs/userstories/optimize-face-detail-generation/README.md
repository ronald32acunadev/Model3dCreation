# Optimize Face Detail Generation (Head Pass)

Speed up and fix the head pass (`Detailed face` / `scripts/kit_assembly/run.py`): eliminate redundant upfront 3D generation of extra seeds and fix candidate selection in `kit.py` so high-quality face details are kept instead of discarded.

## Background & Root Causes

During recent runs (e.g. `data/kit_runs/01M7R7BF05GPHESCV599QTSVFK`), the model generation took ~15 minutes total, with ~10 minutes spent in `kit_assembly/run.py`:

1. **Redundant extra seed generation**: `scripts/kit_assembly/run.py` defaulted `--extra-seeds` to `"11"`. For every job, it generated two full 3D models with TRELLIS.2 (`head_bust` taking 230 s and `head_bust@11` taking 160 s), consuming 6.5 minutes in 3D generation alone.
2. **Bug in candidate selection**: In `scripts/kit_assembly/kit.py` (line 429), the selection loop tested `if ratio > best[2]: best = ...`, picking whichever candidate had the **highest solid-volume ratio** instead of the closest to 1.0. For `head_bust`, the ratio was 0.9994 (near-perfect match) with a 1.08 cm alignment distance. For `head_bust@11`, the ratio was 2.75 (severely bloated) with a 5.67 cm distance. `kit.py` selected `head_bust@11`, which then failed the quality gate (`gap > MAX_HEAD_GAP` of 2.5 cm), discarding the sculpted head entirely and falling back to the low-detail mold!
3. **Outcome**: The entire 6.5 minutes of head sculpting was wasted, and the final model ended up without the high-detail face.

By defaulting `--extra-seeds` to `""` (generating extra seeds only when needed or requested) and selecting the best candidate by volume proximity to 1.0 and alignment tolerance, head pass duration is reduced by ~50% (~3-4 minutes saved per job) and the detailed face is reliably applied.

## Execution Mode
- Approved Mode: **All stories** ("EVERYONEEE!!!!!!!!!!!")
- Status: **Complete**

## Stories

### US-01: Select Best Candidate Head by Proximity to Mold and Alignment Tolerance
- **As a** 3D modeler using the app,
- **I want** the kit assembly tool to pick the candidate head that best matches the mold volume and passes alignment tolerance,
- **so that** bloated or distorted extra candidates are not picked over near-perfect ones, and the high-detail face is successfully applied.

#### Acceptance Criteria
- [x] `US-01 / AC-01`: Given candidate heads with different solid volume ratios (e.g. 0.9994 vs 2.75), `kit.py` selects the candidate whose ratio is closest to 1.0 (`abs(ratio - 1.0)`) within acceptable volume bounds (`0.5 <= ratio <= 1.6`), rather than the maximum ratio.
- [x] `US-01 / AC-02`: Given candidate heads where one passes alignment tolerance (`gap <= MAX_HEAD_GAP`) and another exceeds it, the candidate passing alignment tolerance is preferred.
- [x] `US-01 / AC-03`: When a candidate satisfies volume and alignment criteria, `kit.py` applies the sculpted head and blends the neck instead of skipping due to a bloated alternative.

#### Dependencies
- None.

#### Verification Method
- Unit tests in `scripts/kit_assembly/tests/test_candidate_selection.py`.
- Verified passing with 4 new tests.

---

### US-02: On-Demand Extra Seeds in Head Pass Script
- **As a** user generating 3D models with `Detailed face` enabled,
- **I want** the head pass to sculpt only the primary seed by default,
- **so that** jobs are not slowed down by ~3 minutes of redundant 3D generation when the primary head is already valid.

#### Acceptance Criteria
- [x] `US-02 / AC-01`: In `scripts/kit_assembly/run.py`, `--extra-seeds` defaults to `""` (empty string), so only the primary seed is sculpted by default.
- [x] `US-02 / AC-02`: If `--extra-seeds` is passed on the command line, those extra seeds are included as candidates.
- [x] `US-02 / AC-03`: When the primary seed succeeds, `run.py` proceeds to alignment and kit blending without executing unnecessary extra TRELLIS.2 jobs.
- [x] `US-02 / AC-04`: Update `scripts/kit_assembly/tests/test_quality_gate.py` to match the new `--extra-seeds` default while testing both single-seed and multi-seed paths.

#### Dependencies
- Depends on `US-01` (proper candidate selection when multiple candidates exist).

#### Verification Method
- Unit tests in `scripts/kit_assembly/tests/test_quality_gate.py`.
- Full pytest suite in `scripts/kit_assembly/tests/` (40 passed).
- Full engine pytest suite in `engine/tests/` (434 passed).

---

## QA Evidence and Traceability

| Story / criterion or risk | Test file and test name, or reproducible manual procedure | Observed result and evidence |
| --- | --- | --- |
| `US-01 / AC-01` | `scripts/kit_assembly/tests/test_candidate_selection.py::test_select_candidate_prefers_ratio_closest_to_one` | Passed (prefers 0.9994 over 2.75) |
| `US-01 / AC-02` | `scripts/kit_assembly/tests/test_candidate_selection.py::test_select_candidate_prefers_aligned_candidate` | Passed (prefers candidate <= MAX_HEAD_GAP) |
| `US-01 / AC-03` | `scripts/kit_assembly/tests/test_candidate_selection.py` / `kit.py` candidate selection | Passed |
| `US-02 / AC-01` | `scripts/kit_assembly/tests/test_quality_gate.py::test_default_extra_seeds_is_empty` | Passed |
| `US-02 / AC-02` | `scripts/kit_assembly/tests/test_quality_gate.py::test_head_pass_passes_seeds_to_generator_when_extra_seeds_provided` | Passed (passes `--seeds 42,11,22`) |
| `US-02 / AC-03` | `scripts/kit_assembly/tests/test_quality_gate.py::test_head_pass_runs_with_single_seed_when_no_extra_seeds` | Passed (only sculpts primary seed) |
| `US-02 / AC-04` | `cd scripts/kit_assembly && ../../engine/.venv/bin/python -m pytest tests` | Passed: 40 passed in 16.52s |
| Regression Risk: Existing engine tests | `uv run --project engine pytest engine/tests/` | Passed: 434 passed in 30.79s |
