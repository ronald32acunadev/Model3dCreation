# Remove the Blender add-on

The user decided on 2026-11-19 to drop the Model3D Blender add-on (`blender/`, the extension in `blender/model3d/` and its uv test project) and keep using the Blender installed on the system. The folder is already deleted from the working tree. What still refers to it fails: `client/tests/test_addon_library_compat.py` (1 failure) and `packaging/tests/test_build_deb.py` (7 errors, `build.sh` line 77 `cd .../blender/model3d: No such file or directory`).

Out of scope: the engine's `bpy` wheel, `scripts/kit_assembly` (they use Blender's Python API, not the add-on), the system Blender, and the `model3d` extension already installed in the user's Blender (removing it there is the user's choice, from Blender's Preferences).

Execution mode: all stories (approved 2026-11-19). Status: complete.

## US-01: The setup script no longer installs the add-on

As a user running `scripts/setup/setup-ubuntu.sh`, I want it to stop building and installing the add-on, so that setup works without `blender/`.

- `US-01 / AC-01`: Given a repository without `blender/`, when `setup_repo.py` builds its plan, then no `blender-addon` step is in it.
- `US-01 / AC-02`: `DEV_PROJECTS` no longer lists `blender`, so `dev-tools` syncs `contracts`, `engine`, `client`, `packaging` only.
- `US-01 / AC-03`: `--no-blender-addon` and `--blender-bin` are removed along with `blender_steps`, `system_blender`, `blender_series`, `_default_blender_config`, `ADDON_DIR`, and the final "Run the Blender tests" hint; `--help` no longer lists them, and passing one exits with argparse's usage error.
- `US-01 / AC-04` (regression): every other step and option keeps its behavior.

## US-02: The .deb no longer ships the add-on

As someone building the package, I want `packaging/deb/build.sh` to build without `blender/`, so that the `.deb` builds again.

- `US-02 / AC-01`: `build.sh` no longer creates `/opt/model3d/blender/model3d.zip`, and its header and `control.in` description no longer mention the add-on.
- `US-02 / AC-02`: `packaging/tests/test_build_deb.py` passes; the add-on zip test is removed and the contents test no longer expects the zip.

## US-03: The app no longer points to the add-on

As a user of the desktop app, I want Settings to stop showing the add-on's install path, so that it does not point to a file that no longer exists.

- `US-03 / AC-01`: The Settings dialog has no "Blender add-on" label, path field, or copy button; `ADDON_ZIP` and `_copy_addon_path` are removed.
- `US-03 / AC-02`: The Spanish catalog no longer carries that string (`client/scripts/update_translations.sh` regenerates the `.ts` and `.qm`).
- `US-03 / AC-03`: `client/tests/test_addon_library_compat.py` is deleted, and `test_settings_dialog.py` no longer asserts the add-on path; the client suite passes.

## US-04: Docs match the project

As a reader of the repository docs, I want them to stop describing the add-on, so that they match what exists.

- `US-04 / AC-01`: `README.md`, `README.es.md` (if present), and `CLAUDE.md` no longer list `blender/` as a project or describe the add-on, its tests, or its license line.

Dependencies: none between stories; US-04 last.

## Verification

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| `US-01 / AC-01..AC-04` | `client/tests/test_setup_repo.py` (add-on tests removed, plan test without `blender-addon`, `--no-blender-addon` rejected); `setup-ubuntu.sh --dry-run` | Passed: new tests `test_plan_has_no_blender_addon_step` and `test_blender_addon_options_are_gone` (both flags) failed before (no `SystemExit`, plan had `blender-addon`), pass after; `test_setup_repo.py` 40 passed; dry run lists no `blender-addon` |
| `US-02 / AC-01..AC-02` | `cd packaging && uv run pytest` | Passed: every test errored before (`build.sh` could not `cd blender/model3d`); 6 passed after, with a new assertion that no package path starts with `/opt/model3d/blender` |
| `US-03 / AC-01..AC-03` | `cd client && uv run pytest` | Passed: the new `not hasattr(dialog, "addon_edit")` check failed before, passes after; `test_addon_library_compat.py` deleted; `update_translations.sh` dropped the add-on strings (0 left) and surfaced 7 already-committed strings that lacked Spanish translations, now translated; client 441 passed |
| `US-04 / AC-01` | `git grep -n "blender/model3d\|Blender add-on"` outside `docs/userstories` returns nothing | Passed after also dropping an add-on mention in a `viewmodels/main.py` docstring |
| Full regression | contracts, engine, client, packaging suites | Passed: contracts 43, engine 434, client 441, packaging 6; no failures |
