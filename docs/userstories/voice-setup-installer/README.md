# Voice Stage Setup Installer (Qwen3-TTS & Whisper)

Status: Complete (US-01..US-03 approved 2026-11-17, execution mode: all stories, completed and verified). Branch: `feat/material-ui`.

## Context

Request (verbatim, 2026-11-17): "Qwen3-TTS no esta instalado deberia incluirse en la descarga del setup"

While the Voice Studio section and engine stage exist, the setup recipe (`client/src/model3d_client/setup/recipe.py`) currently only manages `base` (Hunyuan3D/TRELLIS) and `skeletons` (UniRig). Qwen3-TTS and Whisper weights are missing from the install recipe, leaving the Voice section showing "Qwen3-TTS is not installed...".

This feature implements the setup installer for the voice stage (fulfilling US-07 of `docs/userstories/voice-studio/`):
- Component name: `voice` (~8.5 GB: Qwen3-TTS 1.7B VoiceDesign, Qwen3-TTS 1.7B Base, Whisper-large-v3-turbo).
- Environment: `vendor/Qwen3-TTS/.venv` with Python 3.11, PyTorch (cu130), `qwen-tts`, `soundfile`, `librosa`, and dependencies.
- Wizard UI: selectable component checkbox in setup wizard, with download progress, resumable step fingerprints, and done checks.
- App integration: "Install voice models" button in `VoicePanel` when the voice stage is missing, launching or directing to setup.

## US-01: Voice Stage Setup Recipe Steps

As a user, I want the setup runner to automatically install Qwen3-TTS and its weights, so that I don't have to configure environments or download Hugging Face models by hand.

- US-01 / AC-01: `build_recipe` in `client/src/model3d_client/setup/recipe.py` supports the `"voice"` component in `components`.
- US-01 / AC-02: Recipe adds steps when `"voice"` is selected:
  1. `voice-source`: Fetches or creates `vendor/Qwen3-TTS` source root.
  2. `voice-env`: Creates `.venv` under `vendor/Qwen3-TTS` and installs PyTorch (`torch`, `torchvision` from PyTorch cu130 index) and runtime dependencies (`qwen-tts`, `soundfile`, `librosa`, `transformers`, `huggingface_hub`).
  3. `voice-weights`: Downloads the VoiceDesign (`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`), Base (`Qwen/Qwen3-TTS-12Hz-1.7B-Base`), and Whisper (`openai/whisper-large-v3-turbo`) weights into `data/hf`.
- US-01 / AC-03: Each voice step has a deterministic fingerprint and a `done_check` allowing resumption and repair without re-downloading existing checkpoints.
- US-01 / AC-04: Unit tests in `client/tests/test_setup_recipe.py` verify the generated steps and done checks when `"voice"` is present or absent.

## US-02: Setup Wizard Component Selection & Sizing

As a user running the setup wizard, I want to see "Voice studio (Qwen3-TTS)" in the component list with its required disk space, so that I can choose whether to install it.

- US-02 / AC-01: `COMPONENT_SIZES["voice"]` is defined (~8.5 GB) and included in the total required space calculation.
- US-02 / AC-02: `SetupWizard` components page includes a checkbox for `voice` alongside `skeletons`, checked by default when disk space allows.
- US-02 / AC-03: `SetupViewModel` tracks `voice` component state, saving it to install state (`components` list in pointer).
- US-02 / AC-04: Wizard tests in `client/tests/test_setup_wizard.py` and `client/tests/test_setup_vm.py` cover component selection.

## US-03: Voice Panel Setup Action when Not Installed

As a user in the Voice section, when Qwen3-TTS is missing, I want a button that triggers setup for the voice models, so that I can install them directly from the app.

- US-03 / AC-01: When `voice_reason` indicates Qwen3-TTS is missing, `VoicePanel` displays an "Install voice stage" button next to the banner.
- US-03 / AC-02: Clicking the button triggers an action or wizard dialog in update/resume mode targeting the voice component.
- US-03 / AC-03: Existing client and engine tests continue to pass without regressions.

## QA Evidence and Traceability

| Story / criterion or change-related regression risk | Test file and test name, or reproducible manual procedure | Observed result and evidence |
| --- | --- | --- |
| `US-01 / AC-01..AC-04` | `client/tests/test_setup_recipe.py` (`test_voice_component_adds_source_env_weights_steps`, `test_without_voice_component_has_no_voice_steps`, etc.) | Passed: 22 passed in `test_setup_recipe.py` |
| `US-02 / AC-01..AC-04` | `client/tests/test_setup_wizard.py`, `client/tests/test_setup_vm.py` (`test_components_page_has_voice_checkbox_and_updates_vm`, `test_voice_toggle_updates_components_and_needed_bytes`, etc.) | Passed: 23 passed in `test_setup_vm.py` and `test_setup_wizard.py` |
| `US-03 / AC-01..AC-03` | `client/tests/test_voice_panel.py` (`test_install_button_visibility_matches_missing_voice_state`, `test_install_button_emits_install_voice_requested`, `test_install_voice_requested_opens_setup_dialog_and_restarts_engine`) | Passed: 15 passed in `test_voice_panel.py`, full suite (434 passed) clean |
