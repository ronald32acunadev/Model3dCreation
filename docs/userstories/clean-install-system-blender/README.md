# Clean Installation & System Blender Integration

Ensure repository setup scripts (`scripts/setup/setup-ubuntu.sh`, `scripts/setup/setup_repo.py`, etc.) support a complete, reliable clean installation on fresh clones—downloading and configuring all required engine components, vendor environments, AI models, and dev dependencies—while removing the bundled Blender download in favor of the system-installed Blender already present on the PC.

## Background & Objectives

1. **Clean Installation**:
   - When setting up the project on a clean machine or fresh clone, executing the setup scripts must download and prepare *all* required project components:
     - Core engine Python 3.11 environment, PyTorch with CUDA 13, `hy3dgen`, `spconv`, `flash-attn`.
     - AI models: Hunyuan3D-2.1 weights, BiRefNet general ONNX background removal, RealESRGAN inpainting checkpoint.
     - UniRig source, virtual environment, and pretrained weights.
     - TRELLIS.2 source, virtual environment (with `o-voxel` and `curope`), and weights (including dinov3).
     - Qwen3-TTS & Whisper voice source, virtual environment, and weights (`VoiceDesign`, `Base`, `whisper-large-v3-turbo`).
     - Dev dependencies synced via `uv` across all packages (`contracts`, `engine`, `client`, `blender`, `packaging`).
     - Operating system libraries (ensuring `setup-ubuntu.sh` covers compiler tools, X11/OpenGL, and audio libraries like `libsndfile1` and `ffmpeg`).

2. **System Blender (No Bundled Blender Download)**:
   - Previously, `scripts/setup/setup_repo.py` attempted to download a standalone Blender 4.5.15 tarball (~500 MB) into `vendor/blender`.
   - The user has Blender already installed on the host PC (e.g. `/snap/bin/blender` Blender 5.3.0, connected via MCP).
   - Bundling or downloading Blender 4.5.15 is redundant and misaligned with the active system Blender.
   - The setup scripts must eliminate the Blender download step entirely and instead locate the system Blender (`blender` on `PATH`), detect its version series (e.g. `5.3`), and use it to install the `model3d` extension into the user's active Blender extensions directory. A `--no-blender-addon` option must also allow opting out of addon installation.

## Stories

### US-01: Remove Bundled Blender Download & Use System Blender
- **As a** developer or user setting up the project,
- **I want** setup scripts to use the system Blender installed on the PC instead of downloading a redundant standalone Blender 4.5.15 archive into `vendor/blender`,
- **so that** hundreds of megabytes of redundant downloads are avoided, the host Blender connected via MCP is utilized, and the Model3D add-on is installed into the active system Blender environment.

#### Acceptance Criteria
- `US-01 / AC-01`: Remove `BLENDER_URL`, `BLENDER_SHA256`, and the `blender` download step (`Step("blender", ...)`) from `scripts/setup/setup_repo.py`.
- `US-01 / AC-02`: In `setup_repo.py`, `Options` supports `blender_addon: bool = True` and a `--no-blender-addon` CLI flag to opt out of extension installation.
- `US-01 / AC-03`: `blender_steps` detects the system Blender binary (`shutil.which("blender")` or `options.blender_bin`). If found, it determines the major.minor series (e.g. `5.3` from `blender --version`) and installs the `model3d` extension into `options.blender_config / <series> / extensions/user_default/model3d`.
- `US-01 / AC-04`: Update `client/tests/test_setup_repo.py` to remove obsolete `vendor/blender` download assertions and verify system Blender detection, version resolution, and add-on installation behavior.
- `US-01 / AC-05`: `cd blender && uv run pytest -m blender` and `uv run pytest` pass cleanly using the system Blender without relying on `vendor/blender`.

#### Dependencies
- None.

#### Verification Method
- Run `cd client && uv run pytest tests/test_setup_repo.py`.
- Run `cd blender && uv run pytest -m blender`.

---

### US-02: Orchestrate Complete Clean Installation
- **As a** developer running setup on a clean clone or system,
- **I want** `setup-ubuntu.sh` and `setup_repo.py` to download and configure every required dependency, engine, model, and dev tool,
- **so that** a clean checkout becomes fully functional for 3D generation, rigging, voice, and client execution with a single command.

#### Acceptance Criteria
- `US-02 / AC-01`: `scripts/setup/setup-ubuntu.sh` checks and installs all prerequisite system libraries, adding `libsndfile1` and `ffmpeg` for audio processing alongside compiler and GUI packages.
- `US-02 / AC-02`: `scripts/setup/setup_repo.py` defines a complete dependency-ordered step plan for clean checkouts:
  1. `cuda-toolkit`
  2. `hunyuan-source`
  3. `engine-env`
  4. `inpaint`
  5. `dev-tools`
  6. `weights` (Hunyuan3D-2.1 + BiRefNet ONNX)
  7. `unirig-source`, `unirig-env`, `unirig-weights`
  8. `trellis-source`, `trellis-env`, `trellis-weights` (including dinov3)
  9. `voice-source`, `voice-env`, `voice-weights` (VoiceDesign, Base, Whisper)
  10. `blender-addon` (using system Blender)
- `US-02 / AC-03`: Ensure all required data directories (`data/hf`, `data/downloads`, `data/u2net`, `data/setup-logs`) are created reliably during setup.
- `US-02 / AC-04`: Running `client/.venv/bin/python scripts/setup/setup_repo.py --dry-run` displays all clean-install steps accurately without missing dependencies or broken links.

#### Dependencies
- Depends on `US-01`.

#### Verification Method
- Execute dry run and verify step output: `client/.venv/bin/python scripts/setup/setup_repo.py --dry-run`.
- Verify full test suites pass: `cd client && uv run pytest`, `cd engine && uv run pytest engine/tests/test_voice_stage.py engine/tests/test_speech.py`.

---

## Execution Mode & Status

- **Execution Mode**: All stories (approved by user).
- **US-01 Status**: Complete ✅
- **US-02 Status**: Complete ✅

## QA Evidence and Traceability

| Story / criterion or risk | Test file and test name, or reproducible manual procedure | Observed result and evidence |
| --- | --- | --- |
| `US-01 / AC-01..AC-04` | `client/tests/test_setup_repo.py` (`test_linux_steps_in_order`, `test_no_blender_addon_drops_blender_addon_step`, `test_blender_addon_builds_then_installs`, `test_blender_addon_done_matches_the_source_version`) | Passed (37/37 passed in 0.72s) |
| `US-01 / AC-05` | `cd blender && uv run pytest -m blender` & `cd blender && uv run pytest` | Passed (2/2 smoke tests passed with system Blender 5.3; 29/29 general blender tests passed) |
| `US-02 / AC-01` | Inspect `scripts/setup/setup-ubuntu.sh` package definitions | Passed (`libsndfile1` and `ffmpeg` added) |
| `US-02 / AC-02..AC-04` | `client/.venv/bin/python scripts/setup/setup_repo.py --dry-run` and live execution | Passed. All 16 steps planned and executed: `cuda-toolkit`, `hunyuan-source`, `engine-env`, `inpaint`, `dev-tools`, `weights`, `unirig-source`, `unirig-env`, `unirig-weights`, `trellis-source`, `trellis-env`, `trellis-weights`, `voice-source`, `voice-env`, `voice-weights`, `blender-addon` all report `done`. |
| Regression Risk: Client tests | `cd client && uv run pytest` | Passed (435/435 passed, 5 deselected in 69.79s) |
| Regression Risk: Engine tests | `uv run --project engine pytest engine/tests/test_voice_stage.py engine/tests/test_speech.py` | Passed (33/33 passed in 2.53s) |
