# Dark TRELLIS.2 models

Status: **complete** (US-01, US-02). Approved 2026-11-03, all stories. Branch: `fix/dark-trellis-models`.

TRELLIS.2 models look much darker in the app than their input images, and their backs look black.

## Experiment (2026-11-02, overnight)

Seven inputs: varsity, knight, police-night, axe-snow and space-marine (the dark set), plus penguin and person-photo (controls).

The matrix had 77 TRELLIS.2 runs and 0 errors:

- Each image ran with seeds 42, 7 and 123.
- Input exposure was varied three ways: `none`, `gamma` (subject luminance mean → 0.45) and `levels` (p1–p99 stretch).
- Each image also had one `512` run and one fp32-decoder run.
- The 5 models the engine had already produced served as the baseline.

Every model was rendered with Blender Cycles from 4 sides under a uniform white environment, with metallic left `raw`, set to `zero` or `scaled` ×0.3. Each render was then compared with the input on the view whose silhouette matched best.

- Full table: [evidence/experiment-report.md](evidence/experiment-report.md).
- Contact sheets: [evidence/](evidence/). Each one shows the input, the engine's current model front and back, and the best variant front and back.

### Findings

1. **The model itself is not dark.** Under environment light, the engine's current models (`raw`) average a luma ratio of **0.96** against the input, and their back is about as bright as their front (back/front **1.02**). The black look comes from the app's viewer. It has no environment map, so surfaces that TRELLIS marks as metal have nothing to reflect and render almost black, especially on the side the single directional light does not reach.
2. **TRELLIS's metallic prediction changes a lot with the seed.** For axe-snow, the median metallic is 0.999 with seed 42, 0.283 with seed 7 and 0.393 with seed 123. For knight it is 0.999 with seed 42 and 0.543 with seed 123. Across seeds the luma ratio for one image spans 0.52–0.88 (axe-snow) and 0.72–1.19 (knight). That spread is larger than the spread between any two configurations.
3. **Brightening the input with `gamma` makes results worse.** It ranked last for every metallic variant: loss 27–29 against 24 for `none`, with luma ratio 1.32–1.41 (washed out). `levels` comes out almost even with `none` (24.85 against 24.35), so it is not worth adding.
4. **An fp32 decoder brings no meaningful gain.** It produces the same metallic (0.999 against 1.0), runs 0–30 s slower, and its loss of 23.3–24.8 sits within noise of fp16.
5. **Forcing metallic to 0 or ×0.3 does not win on average.** The averages are raw 24.35, ×0.3 24.94 and zero 26.19, and the best variant for 6 of 7 images was `raw`. The exception is axe-snow seed 42, where TRELLIS marked skin as metal and `zero` was better.
6. **`512` is not darker than `1024_cascade`.** It won for space-marine and person-photo and runs in 41 s against 90 s, but the difference is within seed variance.

### Decision on material detection

Detecting materials from the image is **not justified by this data**:

- Where TRELLIS gets metal wrong, it does so for a single seed.
- Under correct lighting, the predicted metal looks right for armor and axes.
- A heuristic detector would add a second model and its own errors, without fixing the real cause (the viewer).

Revisit it only if, after US-01, the models still look wrong when viewed in the app.

## Stories

### US-01: Viewer environment lighting

As a user, I want the 3D viewer to light models the way a neutral studio would, so that metal and the back of a model look like the input image instead of black.

- Scope:
  - `client/src/model3d_client/viewer/viewer.js`.
  - Vendor `three/addons/environments/RoomEnvironment.js` from `three@0.186.1`, and update `VENDOR.md`.
- Out of scope: generation, Blender add-on rendering.
- **US-01 / AC-01:** Given a loaded GLB, when the viewer starts, then `scene.environment` is set from a `PMREMGenerator` built on `RoomEnvironment`.
- **US-01 / AC-02:** Given the space-marine baseline model, when it is shown from the back, then the back/front ratio of the mean luminance of the model pixels is within ±0.1 of the same ratio in a Blender Cycles render under a uniform white environment (0.70). Revised 2026-11-03, with user approval: the original ≥ 0.8 threshold was not measured, and the model's back albedo really is darker.
- **US-01 / AC-03:** Existing viewer behavior (load, orbit, skeleton display) keeps passing its tests.
- Verification:
  - `uv run pytest -m webengine`, with a new test that checks `scene.environment` and front/back luminance through a screenshot of the page.
  - A manual look at the 5 dark models.

### US-02: New-seed regeneration from the model view

As a user, I want to regenerate a model with another seed in one click, so that I can get rid of a bad material roll (for example, skin marked as metal) without retyping anything.

- Depends on: US-01 (judge results under correct light first).
- Confirmed fact: `seed` already exists in `contracts/jobs.py` (request and result) and in the library entry. This story only adds the action.
- **US-02 / AC-01:** Given a finished model, when the user picks "Regenerate with new seed", then a job is submitted with the same image and options and a seed that differs from the stored one.
- **US-02 / AC-02:** The new model is saved as a separate library entry, and the original stays unchanged.
- Verification: client tests against the fake engine (`client/tests/conftest.py`) checking the submitted request and the library contents.

### Not proposed (rejected by the data)

- Input exposure normalization (`gamma`/`levels`).
- An fp32 texture decoder.
- A global metallic cap or scale.
- Image-based material detection.

## Related, separate feature: 32 GB RAM

This was measured during the baseline. It is not part of this backlog because it is a different outcome:

- The TRELLIS worker peaks at about 24 GB while baking.
- The shape-stage process peaks at about 9.3 GB during CPU rembg.
- The system peaks at about 44 GB.

It can become its own backlog (`ram-32gb`) after this one.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `client/tests/test_viewer_widget.py::test_metallic_model_is_not_rendered_black`: grey cube, metallic 1 vs 0, luma ratio ≥ 0.4. It reads the rendered pixels, so it proves the environment light is active rather than only that a property is set. | Passed. RED before the change: 0.049 vs 0.398 (0.12). GREEN after it: 3 webengine tests pass (prototype measured 0.308 vs 0.628, 0.49). |
| US-01 / AC-02 | Manual: the space-marine baseline GLB and a copy rotated 180°, loaded in the real viewer (wayland). Mean luma of the model pixels is read from `ViewerWidget.grab()`. | **Passed (revised criterion).** Before: front 0.105, back 0.067 (0.64). After: front 0.247, back 0.180 (0.73), so the model is 2.4–2.7× brighter, but back/front stays below 0.8. Reference: Blender Cycles under a uniform white environment gives 0.70 for the same model, so the back's albedo really is darker (backpack), and 0.8 was an unmeasured guess. 0.73 is within ±0.1 of 0.70. |
| US-01 / AC-03 | `uv run pytest -q` and `QT_QPA_PLATFORM=wayland uv run pytest -m webengine -q` | Passed: 281 passed; webengine 3 passed. |
| US-02 / AC-01 | `test_generation_vm.py::test_regenerate_submits_same_image_and_options_with_new_seed` | Passed. RED: missing `can_regenerate`/`regenerate`; GREEN: 25 passed in the VM and panel files. |
| US-02 / AC-02 | `test_generation_vm.py::test_regenerate_keeps_source_entry_unchanged` | Passed. |
| US-02 regression: refused for a failed entry, menu order | `test_regenerate_refused_for_failed_entry`, `test_library_panel.py::test_menu_lists_add_walk_after_add_skeleton` (updated list), `test_regenerate_follows_can_regenerate`, `test_regenerate_disabled_by_default` | Passed. The Spanish catalog was updated: 174/174 translated, `test_i18n.py` passes. |
