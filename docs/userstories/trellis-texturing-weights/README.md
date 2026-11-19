# TRELLIS.2 texturing weights in the setup script

After `scripts/setup/setup-ubuntu.sh` finishes, the engine reports `sharper_texture_reason = TRELLIS.2's texturing weights are missing (ckpts/shape_enc_next_dc_f16c32_fp16.safetensors in .../data/hf/hub/models--microsoft--TRELLIS.2-4B/snapshots)`. `TRELLIS_WEIGHTS_SCRIPT` in `scripts/setup/setup_repo.py` downloads TRELLIS.2 with `allow_patterns` that omit `ckpts/shape_enc_next_dc_f16c32_fp16.*`, and `trellis_weights_done` does not check for it, so the step reports done without the file. The engine's check is `texturing_weights_reason` in `engine/src/model3d_engine/__main__.py`.

Status: complete (approved 2026-11-19, all stories).

## US-01: The setup script installs what "Sharper texture" needs

As a user who installs with the setup scripts, I want them to download TRELLIS.2's shape encoder, so that "Sharper texture" is available in the app without a manual download.

Scope: `TRELLIS_WEIGHTS_SCRIPT` and `trellis_weights_done` in `scripts/setup/setup_repo.py`. Other weights and steps are unchanged.

Acceptance criteria:

- `US-01 / AC-01`: Given the TRELLIS.2 weights script, when it is read, then its `allow_patterns` for `microsoft/TRELLIS.2-4B` include `ckpts/shape_enc_next_dc_f16c32_fp16.*`.
- `US-01 / AC-02`: Given a cache that has every file `trellis_weights_done` checks today but not `ckpts/shape_enc_next_dc_f16c32_fp16.safetensors`, when `trellis_weights_done` runs, then it returns false, so an existing install downloads the file on the next run.
- `US-01 / AC-03` (regression): Given a cache with all those files and the shape encoder, when `trellis_weights_done` runs, then it returns true.

Dependencies: none.

## Verification

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| `US-01 / AC-01..AC-03` | New tests in `client/tests/test_setup_repo.py` | `test_trellis_weights_script_downloads_the_shape_encoder` and `test_trellis_weights_done_requires_the_shape_encoder` failed before the change (pattern missing; done returned true without the encoder), passed after; `test_setup_repo.py`: 41 passed |
| Manual | Rerun `scripts/setup/setup-ubuntu.sh --no-blender-addon`; restart the engine; `/v1/health` reports `sharper_texture: true` | Passed on 2026-11-19: the rerun downloaded `shape_enc_next_dc_f16c32_fp16.safetensors`; after a restart `/v1/health` reported `rig`, `detailed_face`, and `sharper_texture` true. The step itself printed `skipped` because the DINOv3 access probe needs a token in a non-interactive run; DINOv3's weights were already present |
