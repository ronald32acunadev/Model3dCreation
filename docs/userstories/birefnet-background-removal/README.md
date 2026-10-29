# BiRefNet background removal

Status: approved in chat on 2026-10-29 ("Sí, cambialo"), execution mode: all stories.

## Context

The shape stage removes the background with Hunyuan's `BackgroundRemover`, which uses rembg's default `u2net`. On a busy scene image (armored figure holding a gun and a chainsaw sword), u2net left the gun semi-transparent, and the shape model turned it into a flat slab. rembg 2.0.65's `birefnet-general` (MIT) kept both weapons. `bria-rmbg` gave a similar mask but is licensed for non-commercial use only, so it is not used.

## US-01: Shape stage removes backgrounds with BiRefNet

As a user generating a model from a photo with a busy background, I want the background removed with BiRefNet, so that thin props such as weapons survive into the mesh.

Acceptance criteria:

- AC-01: When `remove_background` is on and the input has no transparency, the shape stage removes the background with rembg session `birefnet-general`, not `u2net`.
- AC-02: Inputs that already have transparency are still passed through unchanged.
- AC-03: The setup wizard's weights step downloads `birefnet-general` into `data/u2net`, and its done check requires `data/u2net/birefnet-general.onnx`.
- AC-04: Existing installs re-run the weights step once (its fingerprint changes), so the engine never needs to download the model while the hub is offline.

Dependencies: none.

Verification:

- `client/tests/test_setup_recipe.py`: weights step downloads `birefnet-general` and its done check requires the file (AC-03, AC-04).
- `uv run pytest` in `client/` and `engine/`.
- Manual: restart the engine and confirm a job runs on the reference image (AC-01, AC-02); the GPU stage itself has no unit test.

Status: complete.

Observed results (2026-10-29):

- RED: `test_weights_fetch_the_birefnet_background_model` failed before the recipe change; passes after it.
- `client/`: `uv run pytest` 204 passed. `engine/`: `pytest` 166 passed.
- AC-01 manual check: job `01M649RANTGJ1BB4N527TSNXP1` on the reference JPEG succeeded (shape 1:35, paint 1:11). Both weapons are in the mesh and there is no ground plane; the shape is upright (bounds y -1.0..0.99) where the u2net run was a wide flat base (y -0.45..0.44).
- AC-02 is unchanged code (`_has_transparency` guard) and was not exercised in this run.

## Notes

- onnxruntime in the engine is the CPU build, so BiRefNet takes about 90 s per image on CPU (measured on an 896x1200 image). Moving it to GPU would add `onnxruntime-gpu` and VRAM on the shape card; out of scope.
