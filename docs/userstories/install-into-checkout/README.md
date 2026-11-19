# Install into the source checkout

The setup wizard fails at "App sources" when the install folder is the repository checkout itself: `copy_sources` copies `engine/` onto itself and `shutil` raises `SameFileError` for every file ("... are the same file").

Execution mode: all stories (approved in chat, 2026-11-18).

## US-01: Installing into the checkout skips the source copy

As a developer running the wizard with the install folder set to the repository checkout, I want the "App sources" step to succeed, so that the install can continue with the sources already in place.

Acceptance criteria:

- `US-01 / AC-01`: Given an install folder that resolves to the same path as the app root, when the "App sources" step runs, then it copies nothing, raises no error, and leaves the existing `engine/`, `contracts/`, and `scripts/verify_gpus.py` unchanged.
- `US-01 / AC-02`: Given an install folder different from the app root, when the step runs, then `engine/`, `contracts/`, and `scripts/verify_gpus.py` are copied as before (regression).

Dependencies: none. Status: complete.

## Verification

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| `US-01 / AC-01` | `client/tests/test_setup_recipe.py::test_source_step_skips_copy_when_installing_into_the_app_root` | RED before fix (`shutil.Error` same file), passed after; full client suite 437 passed |
| `US-01 / AC-02` | `client/tests/test_setup_recipe.py::test_source_step_copies_sources_into_another_folder` | Passed before and after the fix |
