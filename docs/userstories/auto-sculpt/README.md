# Automatic sculpting from the input image

Status: awaiting approval (2026-11-12). Branch: `feat/public-api-voice`. Builds on `docs/userstories/sharper-body-texture/` (evaluation tools, "high" quality at 1536) and `docs/userstories/detailed-face-general/` (head pass).

## Context

Requests (verbatim, 2026-11-12): "A ver tengo instalado blender 5.3.0 en mi pc con un mcp ya disponible puedes hacer que la app se conecte cargue el modelo luego que trellis lo genero y ahi lo pula", then, asked what polishing means: "Que esculpa modelo solo".

### Facts checked

- Blender 5.3.0 is installed (`/snap/bin/blender`). The engine already runs Blender's Python (`bpy`, the 4.5 wheel) without a window to bake textures, and the repository's Blender add-on (`blender/model3d/`) talks to the engine.
- No Blender MCP tool is connected to this agent's session. An MCP server lets an AI assistant drive Blender; the app itself does not need it to run Blender.
- An AI assistant moving sculpt brushes through MCP cannot see the surface finely enough to decide where a wrinkle or an eyelid goes; it is out of scope.
- The models already come from TRELLIS.2 at 1536 with 500 000 faces on "high" (sharper-body-texture US-05); TRELLIS.2's shape still differs from the picture's (pose, a turned head), as the projection attempts there showed.

### Approach

A local monocular normal estimator reads the input image and gives, per pixel, the direction the surface faces: wrinkles, cloth folds, eyelids and lips show there. A Blender script then displaces the model's front-facing surface so its normals follow the estimated ones, as a sculptor copies a photo. Which estimator runs well on two 12 GB cards, and how much it improves the models, is not known yet; US-01 measures it before anything is built.

Out of scope: an AI driving Blender's brushes, sculpting what the picture does not show (the back), new generators.

## US-01: Choose a relief estimator (spike)

As the owner, I want two local normal estimators tried on my characters before anything is built, so that the sculpting only goes ahead if it really adds detail.

- US-01 / AC-01: Two candidate estimators that run locally are named with their licence, download size, and VRAM need; each download is asked for with its exact size before it happens.
- US-01 / AC-02: Each runs on the input images of the evaluation set (`data/kit_runs/`, 12 characters); the time, the peak VRAM on one 12 GB card, and a sheet of the estimated normals next to each picture are recorded here.
- US-01 / AC-03: The owner picks one estimator, or stops the backlog here.

## US-02: Sculpt the front from the estimated relief

As a user, I want the model's surface to take the relief my picture shows, so that faces, hair and clothes get their creases and folds without my touching Blender.

- US-02 / AC-01: A headless Blender script displaces the vertices the picture sees (facing the camera, not hidden) along their normals so that the surface's shading in front view follows the estimated normals; hidden, side and back vertices stay, and the displacement fades out toward the silhouette and toward surfaces turned away.
- US-02 / AC-02: The displacement is bounded (a few millimetres on a body about 1 m tall) and smoothed, so it never tears the mesh, flips faces, or opens holes; the face count, holes and flipped faces are checked before and after.
- US-02 / AC-03: Where the model's shape and the picture disagree (a turned head, an arm in another pose), measured as in sharper-body-texture's findings, the region is left unsculpted.

## US-03: Measure the sculpting on the evaluation set

As the owner, I want every character rendered before and after under the same light, so that I judge the sculpting by eye and by numbers.

- US-03 / AC-01: For the 12 characters, untextured and textured renders of the face, the chest and a hand, before and after, and a shading-detail number (high-frequency energy of the untextured front render) are recorded here.
- US-03 / AC-02: The owner marks each character better, equal or worse; a character marked worse sends the story back with the reason.

## US-04: Sculpt from the app

As a user, I want to ask for automatic sculpting when I generate, so that it happens without scripts.

- US-04 / AC-01: The generation panel has an "Auto sculpt" option (Spanish catalog included); with it, a finished generation is sculpted and kept as the model, with the previous one kept as `model_original.glb`, before the skeleton and Detailed face run.
- US-04 / AC-02: When sculpting fails or is skipped, the job succeeds with the model unchanged and the app shows the reason.
- US-04 / AC-03: Engine and client tests cover the option and both paths with stub passes; one run in the app.

## Dependencies

US-01 → US-02 → US-03 → US-04. US-01 can stop the backlog.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-03 | candidate list, approved downloads, runs on the 12 images, owner choice | Pending |
| US-02 / AC-01..AC-03 | unit tests on a synthetic mesh (bounded displacement, hidden vertices unchanged, no flipped faces); mesh checks on the evaluation set | Pending |
| US-03 / AC-01..AC-02 | renders and numbers; owner marks | Pending |
| US-04 / AC-01..AC-03 | engine and client tests with stub passes; a run in the app | Pending |
