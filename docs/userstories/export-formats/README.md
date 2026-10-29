# Export formats

Status: complete.

## Context

Today the desktop app's export (`MainViewModel.export`) copies the library entry's `model.glb` to the chosen path; GLB is the only format. The user asked for more export formats and chose FBX, OBJ + MTL, STL, and USD/USDZ (2026-10-29).

The engine's environment already ships `bpy` 4.5.14, which can import the GLB and write every requested format. A spike on a rigged, textured model (`01M64AEQF3CZX1VAY4SBX5SVZQ`) exported each format in under half a second:

| Format | bpy operator | Keeps texture | Keeps skeleton | Files written |
|---|---|---|---|---|
| FBX | `export_scene.fbx(path_mode="COPY", embed_textures=True, add_leaf_bones=False)` | yes (embedded) | yes | 1 |
| OBJ + MTL | `wm.obj_export(export_materials=True, path_mode="COPY")` | yes | no | `.obj`, `.mtl`, texture images |
| STL | `wm.stl_export()` | no | no | 1 |
| USDZ | `wm.usd_export(export_textures=True)` to a `.usdz` path | yes (packaged) | depends on the viewer | 1 |

USDZ is offered rather than loose `.usd`, because `.usd` writes a separate `textures/` folder while `.usdz` packs everything into one file. That's an assumption to confirm when approving.

Conversion runs in a separate process with the engine's Python (`<repo>/engine/.venv/bin/python`, the interpreter `EngineSupervisor` already starts), so the client gains no dependency and the UI stays responsive. It does not need the GPU or a running engine.

## US-01: Convert a library model to another format

As a user, I want a library model converted to FBX, OBJ, STL, or USDZ, so that I can use it in tools that do not read GLB.

Acceptance criteria:

- AC-01: A conversion script bundled with the client imports the entry's GLB with bpy and writes the target with the operator and options in the table above, chosen by the target's extension (`.fbx`, `.obj`, `.stl`, `.usdz`).
- AC-02: A converter service runs that script with the engine's Python as a `QProcess`, without blocking the UI thread, and reports success with the target path or failure with the script's error output.
- AC-03: An unsupported extension, a missing engine Python, or a script failure is reported as a failure. A failed conversion leaves no partial target file.
- AC-04: Exporting to `.glb` still copies the file, as today, without starting a process.

Dependencies: none.

Verification:

- Unit tests (default run): the converter builds the expected command per format and maps exit codes and stderr to success or failure (fake process). The `.glb` path copies without a process.
- Marked `gpu` test (needs the full engine environment, not a GPU): converting `client/tests/assets/cube.glb` to each format produces a non-empty file. For FBX and USDZ, a re-import with bpy finds a mesh.

## US-02: Choose the format when exporting

As a user, I want the export dialog to offer every supported format and tell me when the export finishes or fails, so that I can pick the format the target tool needs.

Acceptance criteria:

- AC-05: The export file dialog lists GLB, FBX, OBJ, STL, and USDZ filters. The chosen filter sets the extension when the user types a name without one.
- AC-06: While a conversion runs, the export action is disabled and the status shows that it's exporting. On success the status names the written file; on failure an error message shows the reason.
- AC-07: When the model has a skeleton and the user picks OBJ or STL, the dialog warns that the format drops the skeleton (and the texture, for STL). The export still proceeds.
- AC-08: New UI strings go through `tr()` and are translated in the Spanish catalog (`client/scripts/update_translations.sh`).
- AC-09: README documents the export formats and what each keeps.

Dependencies: US-01.

Verification:

- View-model and main-window tests with the fake converter: filters offered, extension applied, busy state, success and failure messages, skeleton warning for OBJ and STL.
- `uv run pytest` in `client/`, plus the `gpu`-marked conversion test.
- Manual: export the rigged space dog to each format from the app, and open the FBX in Blender to confirm the skeleton.

## Execution

Mode: all stories (approved in chat on 2026-10-29). USDZ instead of loose `.usd` was accepted with the backlog.

| Story | Status |
|---|---|
| US-01 | complete |
| US-02 | complete |

Observed results (2026-10-29):

- Implementer: a lightweight hosted subagent (`haiku`), reviewed by the planner. It wrote tests and code together, so RED was not observed for the new tests.
- `client/`: `uv run pytest` 219 passed, 9 deselected. `uv run pytest -m gpu tests/test_convert_real.py` 6 passed (each format from `cube.glb`; FBX and USDZ re-import a mesh).
- Manual: `convert_model.py` turned the rigged space dog (`01M64AEQF3CZX1VAY4SBX5SVZQ`) into FBX; re-importing it gave 1 armature with 28 bones, the meshes, and 1 embedded image.
- AC-08: 11 new strings translated in `model3d_es.ts`, `.qm` recompiled (162 finished, 0 unfinished). AC-09: README "Export formats" table.
- Not checked: exporting from the running app's dialog by hand.
