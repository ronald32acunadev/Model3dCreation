# Voice Stage Setup in Repository Setup Scripts

Incorporate the Qwen3-TTS and Whisper voice stage installation and model download directly into `scripts/setup/setup_repo.py` so that setting up the repository from `scripts/` prepares the voice environment and models alongside the engine, UniRig, and TRELLIS.2.

## Background & Root Causes

When attempting to test or use voices from `scripts/` (e.g. `scripts/voice/try_voice.py`), the process failed because:
1. `scripts/setup/setup_repo.py` (and `setup-ubuntu.sh`) only included setup steps for `cuda-toolkit`, `hunyuan`, `engine`, `unirig`, `trellis`, and `blender`. It completely lacked steps for the `voice` component.
2. `vendor/Qwen3-TTS` and its virtual environment `vendor/Qwen3-TTS/.venv` were never created during repository setup.
3. The model weights (`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`, `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, `openai/whisper-large-v3-turbo`) in `data/hf` were never downloaded.
4. `scripts/voice/try_voice.py` requires `vendor/Qwen3-TTS/.venv/bin/python` and sets `HF_HUB_OFFLINE = "1"`, crashing immediately when run outside an already prepared environment.

By incorporating `voice-source`, `voice-env`, and `voice-weights` steps directly into `scripts/setup/setup_repo.py` (with a `--no-voice` flag to opt out), developers and scripts can install and download all required voice assets in a single command.

## Stories

### US-01: Incorporate Voice Setup into `scripts/setup/setup_repo.py`
- **As a** developer or user setting up the repository via `scripts/setup/`,
- **I want** `scripts/setup/setup_repo.py` to automatically fetch Qwen3-TTS, create its virtual environment, and download the voice models,
- **so that** the voice stage is fully ready without manual environment setup or missing weights.

#### Acceptance Criteria
- `US-01 / AC-01`: `Options` in `scripts/setup/setup_repo.py` includes `voice: bool = True`, and argument parser supports `--no-voice` to skip voice setup if desired.
- `US-01 / AC-02`: `setup_repo.py` defines `voice_steps(repo, platform, env)` producing three steps matching the client recipe:
  1. `voice-source`: Downloads `QWEN_TARBALL` and extracts it into `vendor/Qwen3-TTS`.
  2. `voice-env`: Creates `.venv` under `vendor/Qwen3-TTS` and installs PyTorch (`torch==2.9.1`, `torchvision==0.24.1` with CUDA 13 index) plus `qwen-tts`, `soundfile`, `librosa`, `transformers`, and `huggingface_hub`.
  3. `voice-weights`: Downloads `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`, `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, and `openai/whisper-large-v3-turbo` into `data/hf`.
- `US-01 / AC-03`: `build_steps` on Linux appends `voice_steps` when `options.voice` is enabled, and omits them when `options.voice` is `False`.
- `US-01 / AC-04`: Unit tests in `client/tests/test_setup_repo.py` verify the presence and order of `voice-source`, `voice-env`, and `voice-weights`, as well as `--no-voice` behavior.

#### Dependencies
- None.

#### Verification Method
- `cd client && uv run pytest tests/test_setup_repo.py` passes.

---

### US-02: Execute Voice Setup & Verify Model Download
- **As a** user generating voices and 3D character speech,
- **I want** the voice setup steps executed so `vendor/Qwen3-TTS/.venv` is populated and weights exist in `data/hf`,
- **so that** voice generation and `scripts/voice/try_voice.py` function properly.

#### Acceptance Criteria
- `US-02 / AC-01`: `vendor/Qwen3-TTS/.venv/bin/python` exists and can import `qwen_tts`, `soundfile`, `librosa`, `torch`, and `transformers`.
- `US-02 / AC-02`: `data/hf/hub` contains snapshots for `Qwen3-TTS-12Hz-1.7B-VoiceDesign`, `Qwen3-TTS-12Hz-1.7B-Base`, and `whisper-large-v3-turbo`.
- `US-02 / AC-03`: Running verification confirms the voice stage is ready.

#### Dependencies
- Depends on `US-01`.

#### Verification Method
- Execute setup for voice steps: `client/.venv/bin/python scripts/setup/setup_repo.py`.
- Run verification command to test model import and pipeline readiness.

---

## Execution Mode & Status

- **Execution Mode**: All stories (approved by user).
- **US-01 Status**: Complete ✅
- **US-02 Status**: Complete ✅

## QA Evidence and Traceability

| Story / criterion or risk | Test file and test name, or reproducible manual procedure | Observed result and evidence |
| --- | --- | --- |
| `US-01 / AC-01..AC-04` | `client/tests/test_setup_repo.py` (`test_pins_come_from_the_recipe`, `test_linux_steps_in_order`, `test_no_voice_drops_voice_steps`) | Passed (37/37 tests passed in 0.68s) |
| `US-02 / AC-01..AC-03` | `client/.venv/bin/python scripts/setup/setup_repo.py` | Passed. Steps executed: `voice-source: done`, `voice-env: done`, `voice-weights: done`. |
| `US-02 / AC-01` | `vendor/Qwen3-TTS/.venv/bin/python -c "import qwen_tts, soundfile, librosa, torch, transformers; print(torch.cuda.is_available())"` | Passed. CUDA available: True, Torch version: 2.9.1+cu130, torchaudio: 2.9.1+cu130. |
| `US-02 / AC-02` | Inspection of `data/hf/hub` snapshots | Passed. Snapshots present for `Qwen3-TTS-12Hz-1.7B-VoiceDesign`, `Qwen3-TTS-12Hz-1.7B-Base`, `whisper-large-v3-turbo`. |
| `US-02 / AC-03` | `vendor/Qwen3-TTS/.venv/bin/python scripts/voice/try_voice.py --help` | Passed. Option menu and arguments displayed without errors. |
| Regression Risk: Existing client tests | `cd client && uv run pytest` | Passed (435/435 passed, 5 deselected). |
| Regression Risk: Existing engine tests | `uv run --project engine pytest engine/tests/test_voice_stage.py engine/tests/test_speech.py` | Passed (33/33 passed). |
