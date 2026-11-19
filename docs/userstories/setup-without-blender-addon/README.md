# Setup without the Blender add-on sources

The user removed `blender/` (the Model3D Blender add-on sources) from their checkout and uses the Blender installed on the system. `scripts/setup/setup_repo.py` then stops at `dev-tools`, which runs `uv sync` in every project of `DEV_PROJECTS = ("contracts", "engine", "client", "blender", "packaging")`, failing with `[Errno 2] No such file or directory: .../blender`. Every later step (weights, UniRig, TRELLIS.2, voice) is reported as "not run", so "With skeleton", "Sharper texture", and "Detailed face" stay unavailable in the app.

Status: complete (approved 2026-11-19, all stories).

## US-01: Setup continues when a project folder is absent

As a developer whose checkout has no `blender/` folder, I want the setup script to install the test tools for the projects that exist and continue, so that UniRig, TRELLIS.2, and the voice stage still get installed.

Scope: `dev_tools_step` in `scripts/setup/setup_repo.py`. Other steps, `DEV_PROJECTS`, and the add-on step (already skippable with `--no-blender-addon`) are unchanged.

Acceptance criteria:

- `US-01 / AC-01`: Given a repository without `blender/`, when the `dev-tools` step runs, then it runs `uv sync --frozen --inexact` in each existing project, does not run it for `blender`, and does not fail.
- `US-01 / AC-02`: Given a repository without `blender/` whose other projects have their dev tools installed, when `done()` is checked, then it returns true (the missing project is not required).
- `US-01 / AC-03` (regression): Given every project folder present, when the step runs, then it syncs all five projects as before, and `done()` still returns false while any present project lacks its dev tools.

Dependencies: none.

## Verification

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| `US-01 / AC-01..AC-03` | New and existing tests in `client/tests/test_setup_repo.py` for `dev_tools_step` | `test_dev_tools_skips_absent_projects` and `test_dev_tools_done_ignores_absent_projects` failed before the fix (extra `uv sync` in `blender`; `done()` False), passed after; updated `test_dev_tools_syncs_every_project_keeping_packages` passed; `test_setup_repo.py`: 39 passed |
| Manual | `scripts/setup/setup-ubuntu.sh --no-blender-addon` passes `dev-tools` and installs UniRig and TRELLIS.2; engine health reports `rig`, `detailed_face`, `sharper_texture` available | Passed on 2026-11-19 for the setup run: every step done, `vendor/UniRig` and `vendor/TRELLIS.2` installed; health reports `rig` and `detailed_face` available. `sharper_texture` stays unavailable for a separate cause (TRELLIS.2 shape encoder weights not downloaded), tracked in `docs/userstories/trellis-texturing-weights/README.md` |
