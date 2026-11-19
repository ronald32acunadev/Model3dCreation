# Checkout pointer opens development mode

`scripts/setup/setup-ubuntu.sh` and `setup_repo.py` install everything the project and the client need into the repository. Launching the app must not install anything. Today `startup_mode` in `client/src/model3d_client/app.py` reads the install pointer (`$XDG_CONFIG_HOME/model3d/config.json`, fallback `~/.config/model3d/config.json`) and, when it exists, checks the wizard's `install.json` in that folder. A pointer left by an earlier wizard run that names this checkout makes `client/launch.sh` open the setup wizard, which then downloads and builds into the repository.

Status: complete (approved 2026-11-18, all stories). Manual check below.

## US-01: A pointer to this checkout opens the main window

As a developer who installs with the setup scripts, I want `client/launch.sh` to open the main window even when a leftover install pointer names this checkout, so that launching never runs the installer.

Scope: `startup_mode` only. The wizard, the setup scripts, and the pointer file are not changed. A pointer to any other folder keeps today's behavior.

Acceptance criteria:

- `US-01 / AC-01`: Given a pointer whose `install_dir` resolves to the repository root that holds `engine/pyproject.toml`, and no `install.json` in it, when `startup_mode` runs, then it returns `"development"`.
- `US-01 / AC-02`: Given the same pointer written with a different spelling of the same folder (trailing slash or a symlinked path), when `startup_mode` runs, then it returns `"development"`.
- `US-01 / AC-03` (regression): Given a pointer to a different folder, when `startup_mode` runs, then it returns the same mode as before (`missing_folder`, `resume`, `update`, or `main`).
- `US-01 / AC-04` (regression): Given no pointer, when `startup_mode` runs from the repository, then it returns `"development"`.

Dependencies: none.

## Verification

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| `US-01 / AC-01` | `client/tests/test_app.py::test_startup_mode_development_when_pointer_names_the_checkout` | Failed without the fix (returned `resume`), passed with it |
| `US-01 / AC-02` | `client/tests/test_app.py::test_startup_mode_development_when_pointer_is_a_symlink_to_the_checkout` | Failed without the fix, passed with it |
| `US-01 / AC-03` | Existing `startup_mode` tests (`missing_folder`, `resume`, `update`, `main`) | Passed |
| `US-01 / AC-04` | `test_startup_mode_development_from_a_checkout` | Passed. Full client suite: 438 passed, 1 failed (`test_addon_library_compat.py::test_addon_entry_reads_as_client_entry`, unrelated: the user removed `blender/` from the working tree) |
| Manual | `client/launch.sh` with the current pointer opens the main window, not the wizard | Passed on 2026-11-19: with `~/.config/model3d/config.json` naming the checkout, the app's supervisor started the engine (only the main window does) and `/v1/health` answered `ready: true` |
