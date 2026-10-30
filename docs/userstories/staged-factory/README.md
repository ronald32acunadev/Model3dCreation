# Staged factory

Status: paused 2026-10-30 at the user's request ("Para todo lo mejor es seguir en otra session"). US-01 to US-04, US-06, and US-08 work. The assembly/detail step (US-05) is not solved. The best result so far is TRELLIS.2 whole-body `1024_cascade` at 500 000 faces.

## Context

On 2026-10-30 the user designed, over several messages, a staged pipeline to get closer to the reference model's detail (Jill, `/home/pumba/Downloads/jill-re3`). Their words, in order:

- "te parece si usamos la analogia de una figura de coleccion que usualmente viene por partes y luego estas se arman ... hacemos el pulido por cada parte en este caso cabeza,torzo,brazo derecho ,brazo izquierdo , pierna derqcha , pierna izquiera y luego juntar todas ennun solo modelo"
- "Pero tenemos el esqueleto?"
- "primero seria generar un modelo 3d sin texturas ... el molde estructural sin tanto de detalle el que servira para unir , luego se separe en partes y cada una se pule como si fuera un escultor luego se unen todos y se ve el modelo 3d sin texturas lo guardamos en el disco, luego se generan las textura como el modelo de Jill ... guardamos en el disco y luego cuando ocupamos ver se unen ambas , serie como una fabrica en etapas"
- "Hagamos eso junto con la carga y liberacion de modelos por etapa asi tendremos una mejor fabrica , mi analogia seria como construir un carro , te parece si paramos la pruebas y hacemos un plan para esto y luego reanudamos"

In car terms: chassis (mold) → parts → bodywork (sculpting) → assembly line → paint shop → delivery.

What exists:

- TRELLIS.2 in `vendor/TRELLIS.2` (worker `engine/src/model3d_engine/stages/trellis_worker.py`, two-card split, low-VRAM mode). Hunyuan3D 2.1 shape and paint. UniRig rigging. RealESRGAN x4 in the Hunyuan clone.
- The engine already runs stages as processes that load a model, work, and can be unloaded (`ProcessStageRunner`).
- `client/src/model3d_client/services/animate_rig.py` finds legs and arms on UniRig skeletons by bone position and hierarchy.
- Spike tools in `data/spikes/`: front-render fidelity score (`score.py`), head crop + upscale, head merge (`merge_head.py`), hybrid paint (`run_hybrid.py`).

Findings that shaped this plan (`docs/userstories/detail-experiments/`):

- On one 12 GB card the heavy part is decoding and CuMesh baking (about 10 GiB), not the flow models. 500 000 faces with 4096 px textures fit; 8192 px does not.
- Research and our measurements agree: TRELLIS.2 gives sharper geometry, Hunyuan's paint more faithful texture.
- In the test image, the head is only about 180 px wide, a hard ceiling on its detail. A 4× RealESRGAN upscale before generation helps.
- Detail is spent evenly over the whole body. Generating a part alone gives it the whole 1024 grid.

## Stages

Each stage loads only its model, does its work, writes its output to the job folder, and unloads, so the next stage gets the whole GPU. A stage whose output already exists is skipped, so a failed or edited stage can be rerun alone.

| # | Stage (car analogy) | Model | Output in the job folder |
| --- | --- | --- | --- |
| 1 | Mold (chassis) | TRELLIS.2 `512`, geometry only | `mold.glb` |
| 2 | Skeleton | UniRig on the mold | `mold_rig.glb` |
| 3 | Breakdown into parts | script, bones → 6 parts | `parts/<part>.glb`, `crops/<part>.png` |
| 4 | Sculpt each part (bodywork) | RealESRGAN ×4, then TRELLIS.2 `1024_cascade`, geometry only | `sculpted/<part>.glb` |
| 5 | Assembly | script | `model_untextured.glb` |
| 6 | Paint shop | Hunyuan paint: UV unwrap + bake | `model_uv.glb`, `textures/albedo.png`, `textures/metallic_roughness.png` |
| 7 | Delivery | combine mesh + textures; optional final rig | `model.glb` |

## Assumptions to confirm when approving

- Built first as a standalone command-line pipeline under `data/spikes/factory/`. Engine and app integration is a separate, later story (US-09), once the result is worth it.
- Parts are the six the user named: head, torso, right arm, left arm, right leg, left leg. Weapons and props stay with the part they overlap most (for example a gun with the arm holding it).
- A character UniRig cannot split into these six parts (not a biped) falls back to the whole mold sculpted at once, with a recorded reason.
- Texturing is one paint pass over the assembled model, so colors match across parts. Separate per-part textures (a dedicated face texture like Jill's) are a later story, not this plan.
- The test case is the dog-headed marine image; the fidelity score and side-by-side renders next to Jill are the evidence.
- No new model downloads.

## US-01: A stage runner with load and release per stage

As a user, I want the factory to run stage by stage and free the GPU after each one, so that every stage has the whole card and a failed stage can be rerun alone.

- US-01 / AC-01: `factory.py run IMAGE JOB_DIR` runs stages 1 to 7 in order. Each stage runs in its own process that loads its model, writes its outputs, and exits. Before the next stage starts, both GPUs are back to their idle memory, measured and logged.
- US-01 / AC-02: A stage whose outputs exist is skipped. `--from N` reruns from stage N, deleting outputs from N on.
- US-01 / AC-03: Each stage logs its time and peak VRAM per card to `JOB_DIR/factory.log`. A failing stage stops the run with its error, and the outputs of earlier stages are kept.

Verification: one run on the test image; a rerun with `--from 6` repaints only.

## US-02: Mold

As a user, I want a fast untextured mold of the whole character, so that it defines the pose and proportions the parts will follow.

- US-02 / AC-01: Stage 1 runs TRELLIS.2 `512` without the texture flow and decoder, and writes `mold.glb` in Hunyuan's convention: y-up, facing +z, height scaled to [-1, 1].
- US-02 / AC-02: Time and peak VRAM are recorded. The texture steps are expected to be skipped, so this should be lower than the measured 512 run with texture.

## US-03: Skeleton and breakdown

As a user, I want the mold split into head, torso, arms, and legs along its skeleton, so that each part can be sculpted on its own.

- US-03 / AC-01: Stage 2 rigs the mold with UniRig. Stage 3 groups bones into the six parts (reusing the leg and arm detection from `animate_rig.py`), assigns each vertex to the part with the largest skin weight, and writes one GLB per part.
- US-03 / AC-02: Each part's front-projected bounding box, with a margin, gives its crop of the input image (`crops/<part>.png`, background removed).
- US-03 / AC-03: A render with each part in its own color is saved, so the split can be checked by eye.

## US-04: Sculpt parts

As a user, I want each part generated on its own at full resolution, so that every part gets the detail a whole-body run spreads thin.

- US-04 / AC-01: Each crop is upscaled 4× with RealESRGAN, then generated with TRELLIS.2 `1024_cascade`, geometry only, one part at a time, with the model released between the batch and the next stage.
- US-04 / AC-02: Per part, the face count, time, and peak VRAM are recorded. A part that fails is replaced by its mold piece and recorded, without stopping the run.

## US-05: Assembly

As a user, I want the sculpted parts put back on the mold's frame, so that the result is one model with the mold's pose.

- US-05 / AC-01: Each sculpted part is scaled and placed to match its mold part (bounding box and joint position). The overlap with neighbouring parts is trimmed at the joint, and the result is written as `model_untextured.glb`.
- US-05 / AC-02: A front, side, and back render of the assembly is saved. Gaps or misplacements at joints are recorded, and the story stops for a decision if the assembly is visibly broken.

## US-06: Paint shop

As a user, I want the textures made last and kept as separate image files, like the reference model, so that repainting does not redo the geometry.

- US-06 / AC-01: Stage 6 paints `model_untextured.glb` with Hunyuan paint `high`, with the face limit raised to 500 000 (instead of 150 000) so part detail is not decimated away. It writes the UV mesh and the textures as separate PNG files.
- US-06 / AC-02: Stage 7 combines mesh and textures into `model.glb`, which opens in Blender and in the app's viewer.

## US-07: Compare and decide

As a user, I want the factory result measured and shown next to the current pipelines and Jill, so that I can judge whether the factory is worth building into the app.

- US-07 / AC-01: The fidelity score (full body and head) of the factory result, of Hunyuan `high`, and of TRELLIS.2 `1024_cascade` are recorded on the test image.
- US-07 / AC-02: Head and full-body renders of the three, next to the Jill render, are sent to the user, and the user's verdict is recorded.

## US-08: Resume the paused experiments

As a user, I want the paused pipeline tests resumed with what the factory provides, so that their results are not lost.

- US-08 / AC-01: The hybrid result already on disk (`data/spikes/exp/runs/P1`, generated before the pause) is scored and included in US-07's comparison.

## US-09: Factory in the app (later, separate approval)

As a user, I want to choose the factory from the app, once it proves better.

- US-09 / AC-01: Scoped and approved separately after US-07. Not part of this approval.

## Results of the first run (2026-10-30, dog-headed marine, seed 1234)

All seven stages ran end to end with `data/spikes/factory/factory.py`. Each stage ran in its own process, and both cards returned to idle between stages (`factory.log`).

| Stage | Time | Peak VRAM (bus 0000:29:00.0) | Notes |
| --- | ---: | ---: | --- |
| 1 Mold, TRELLIS.2 `512` geometry only | 53 s (14 s generating) | 3 962 MiB | 1.9 M raw faces cut to 148 k |
| 2 Skeleton, UniRig | 55-99 s | 5 520 MiB | |
| 3 Breakdown | 5 s | CPU | 2 legs and 2 arms found; head and backpack together; weapons with the arms |
| 4 Sculpt, RealESRGAN ×4 + TRELLIS.2 `1024_cascade` per part | 335 s | 8 660 MiB | 5.4-9.4 M raw faces per part |
| 5 Assembly | 4-11 s | CPU | first version broken (depth misplaced, holes); fixed with ICP fit (error 0.04-0.08 → under 0.001) and mold patches for uncovered areas |
| 6 Paint, Hunyuan `high` at 500 000 faces | 210 s | 7 590 MiB | textures written as separate files |
| 7 Delivery | 0 s | | |

Bugs found and fixed during the run: the geometry-only calls needed `torch.inference_mode()` (without it, 512 ran out of memory); the paint process hung for 30 min in interpreter teardown after finishing (fixed with `os._exit`); the first assembly was misplaced.

Fidelity scores against the input image (DINOv2 cosine; higher is better):

| Model | Full body | Head |
| --- | ---: | ---: |
| Hunyuan `high` (library, 2026-10-29) | 0.832 | 0.651 |
| TRELLIS.2 `1024_cascade` 150 k (V2) | 0.780 | 0.673 |
| TRELLIS.2 `1024_cascade` 500 k (V3a) | 0.777 | 0.676 |
| Hybrid: V3a geometry + Hunyuan paint (P1) | 0.785 | 0.608 |
| Factory | 0.741 | 0.451 |

Observed:

- The factory's sculpted head is closer to the input than any whole-body run. It has a dog's muzzle and nose, where the whole-body TRELLIS.2 head looks more like a cat's. So sculpting a part alone does add shape detail.
- But the assembled model is a soup of separate, overlapping pieces: the sculpted part, the leftover neighbours, and the mold patches all layer at the seams. It also has faceted shading. Hunyuan's paint then projects onto every layer, which shreds the texture, and that is why the head score dropped.
- Next fix (proposed, not done): after assembly, fuse everything into one closed surface, the way ZBrush's DynaMesh merges pieces, with a voxel remesh such as o-voxel's narrow-band dual contouring. Then smooth the normals and paint.

US-07 verdict: pending.

Scope revision (2026-10-30). Two ideas from the user replaced pasting the pieces. First: "matematicamente tenemos ... como deberia estar pulido el modelo ... guardar en el disco todo lo que matematicamente son los poligonos en varios archivos luego se van leyendo estos archivos como en partes para ir esculpiendo". Second: "cada parte tiene su perimetro pero en su totalidad pertenecen a un perimetro mayor como un rompecabezas , eso es lo que no estamos respetando". The assembly stage becomes a detail stage on the mold:

- The mold is the puzzle. Its skeleton labels split one mesh into six pieces that share their border vertices, so there are no gaps and no overlaps.
- The mold is subdivided (each triangle into four, twice). Borders stay where they are, and border vertices keep their shared status.
- For each piece, one at a time, the aligned sculpted part is loaded as a reference. Every vertex of the piece moves only along the mold's surface normal, toward the reference surface. The move is weighted from 0 at the piece's border to 1 a few rings inside, so borders never move and pieces still fit. The part is then released.
- The result is one mesh with the mold's skeleton labels. The voxel fuse attempt (open sheets fall apart; thickened sheets produce 16 M faces) is abandoned.

## Geometry first, then paint (2026-10-30)

The user asked for the plain two-step flow: "Trellis 2 genera la mejor geometria y huanyan la mejor textura". P1 was not a fair test of it: its paint stage cut the 500 000-face geometry to 150 000 (`white_mesh_remesh.obj` has 150 000 faces).

Runs, seed 1234, test image, all under `data/spikes/exp/runs/`:

- H1: RealESRGAN ×4, TRELLIS.2 `1024_cascade` geometry only, Hunyuan paint `high` at 500 000 faces. Stopped at the user's request ("Paremos todo") after more than 8 min in the paint stage, with 3 threads busy and the GPU idle. No paint output. Hunyuan unwraps with `xatlas.parametrize` on the CPU; that this was the step running is inferred from the order of steps.
- H2: the same, with the UVs computed by CuMesh `uv_unwrap` (GPU charts, xatlas per chart) and handed to the paint stage in place of `mesh_uv_wrap`.

| H2 step | Time | Peak VRAM |
| --- | ---: | ---: |
| Upscale ×4 | 7 s | not separated from the previous process |
| Geometry (7.7 M raw faces cut to 494 242) | 90 s (52 s generating) | 9 172 MiB |
| CuMesh unwrap | 8 s (6 s unwrapping) | 412 MiB |
| Paint | 154 s | 7 594 MiB |

| Model | Full body | Head |
| --- | ---: | ---: |
| Hunyuan `high` | 0.832 | 0.651 |
| TRELLIS.2 `1024_cascade` 500 k (V3a) | 0.777 | 0.676 |
| Hybrid at 150 k (P1) | 0.785 | 0.608 |
| Hybrid at 500 k, CuMesh UVs, ×4 input (H2) | 0.782 | 0.523 |

Observed in the renders (`data/spikes/review/cmp-h2-*.png`):

- Speed: CuMesh solved the unwrap bottleneck.
- Geometry: H2 has a more dog-like muzzle, nose, and lips, and sharper chest detail than V3a. It also looks faceted, probably because the geometry-only path exports no smooth normals and skips the remesh `to_glb` applies. Not checked.
- Texture: armor scratches and markings are as rich as P1 or richer. The head is worse: dark blotchy fur, and eyes that read as black glossy patches.
- This run changed three things at once against P1 (face count, input upscale, UV method), so it does not tell which one hurt the head.

The user then cleared `data/spikes/exp/runs/` and copied the reference to `data/spikes/exp/runs/jill-re3/`, so H1, H2, P1, and the V runs are no longer on disk. The numbers above are the record.

## Texture density against the reference (2026-10-30)

The user's reading of the reference: "la textura de la cara esta separada del cuerpo, o cada parte tiene textura diferentes talvez es ahi donde estamos fallando". Measured with bpy. Density is texture pixels per full body height, at each material's own rate, so models of different scale compare directly.

| Model / material | Texture | Share of surface | Pixels per body height |
| --- | ---: | ---: | ---: |
| Jill, `Face` | 4096 | 0.79 % | 30 707 |
| Jill, `Arms` | 4096 | 3.92 % | 13 300 |
| Jill, `Body` | 4096 | 6.91 % | 8 083 |
| Jill, jeans and shirt | 2048 | 12-8 % | about 2 600 |
| Ours, TRELLIS.2 (library `20261030T052056`) | 4096 | whole model | 2 562 |
| Ours, Hunyuan `high` (library `20261029T171352`) | 2048 | whole model | 1 021 |
| Ours, factory at 500 k | 2048 | whole model | 817 |

Findings:

- The user is right. The reference has 35 materials and spends a whole 4096 px texture on 0.79 % of the surface. Its face is about 12× our TRELLIS.2 density and 30× our Hunyuan density. Its clothes match TRELLIS.2.
- Hunyuan bakes at 4096 px, then halves it when saving: `save_mesh(output_mesh_path, downsample=True)` in `vendor/Hunyuan3D-2.1/hy3dpaint/textureGenPipeline.py:186`. Our Hunyuan models ship 2048 px textures.
- Texture size is only the container. The test image has about 180 px of head width, so more texels alone would hold blur, not detail. The reference's eyes and teeth are also separate geometry with their own materials.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-03 | Full run and `--from 5`/`--from 6` reruns, log with VRAM per stage | Passed |
| US-02 / AC-01..AC-02 | `mold.glb` orientation, scale, time, VRAM | Passed: y-up, [-1, 1], 14 s, 3.9 GiB |
| US-03 / AC-01..AC-03 | Part GLBs, crops, colored-parts render | Passed (`parts.png`) |
| US-04 / AC-01..AC-02 | Per-part results | Passed: 6/6 parts sculpted |
| US-05 / AC-01..AC-02 | Assembly renders | Partial: complete after ICP fix, but overlapping layers at seams |
| US-06 / AC-01..AC-02 | Separate textures; GLB opens in Blender and the viewer | Passed in Blender (renders); not checked in the app viewer |
| US-07 / AC-01..AC-02 | Scores and renders; user verdict | Scores and renders done; verdict pending |
| US-08 / AC-01 | P1 score recorded | Passed |

## Handoff for the next session (2026-10-30)

Where things are:

- Factory code (not versioned; `data/` is gitignored): `data/spikes/factory/`. It holds `factory.py` (stage runner), `trellis_shape.py` (geometry-only TRELLIS.2), `breakdown.py` (skeleton → six parts), `fuse.py` (abandoned voxel fuse), and `detail.py` (puzzle displacement). The test job is `data/spikes/factory/jobs/dogmarine/`; renders are in `data/spikes/review/`; the fidelity score is `data/spikes/score.py`.
- Working: mold in 14 s, UniRig skeleton, six-part breakdown, per-part sculpt (RealESRGAN ×4 + TRELLIS.2 `1024_cascade`), ICP alignment of parts to the mold, Hunyuan paint with separate texture files, and per-stage process release.
- Not working: putting detail back on one surface.
  - Pasting parts gives overlapping layers, which shred the paint.
  - A voxel fuse needs closed volumes; the parts are open sheets.
  - Puzzle displacement (`detail.py`: weld, own midpoint subdivision, pinned borders, move along normals) shows the dog's nose and muzzle but leaves a spiky surface. Only about half of the vertices find a valid target, because the 512 mold and the sculpted parts disagree in shape.
- Ideas not tried yet:
  - Use the TRELLIS.2 `1024_cascade` whole-body mesh as the base instead of the 512 mold, so the base is closer to the parts.
  - Transfer detail only where the part and the base agree, within a small distance.
  - Ray-cast along normals instead of nearest points.
  - Multi-view input (front, side, and back images), which needs a model download (MV-Adapter or Hunyuan3D-2mv).
- Uncommitted: one line in `engine/src/model3d_engine/stages/trellis_worker.py` (per-job `texture_size`), plus `docs/userstories/detail-experiments/` and this folder.
