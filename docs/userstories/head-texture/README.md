# Head texture

Status: the user chose the head in its seat on 2026-10-30. Chosen model `N_S3_sides`, index 69.3. It rigs and walks. Weight smoothing is in the engine; the head pipeline is not.

Authorization (verbatim): "Te dejo trabajando el resto de la noche debo ir a dormir , para que auto crees ramas en git cuando consideres hacer un cambio para mejorar el modelo 3d y si creo que puedes correr en paralelo para reducir el tiempo de ejecucion y aprovechar los recursos de la pc"

This stands in for the usual backlog approval, for all stories in order. Limits taken from it: new local branches only; no push, no merge, nothing deleted.

Target (verbatim): "Es jill es el 100 , Tratemos de alcanzar un 70% de la calidad de jill , luego si lo conseguimos un 75% si lo conseguimos un 80% ahi seria un aprobado"

Branches, revised (verbatim): "Espera la creacion de rams creo que hagamosla cada vez que se logra el porcentaje indicado". A branch is made only when the index reaches 70, 75, or 80. This replaces US-03 / AC-01's condition.

User's comment during the run (verbatim): "Ok un 60 -  65% esta bien faltan las texturas". Read as: the current result is about 60-65 % by eye, and textures are what is missing. The work therefore stays on textures. The 70, 75, and 80 targets and the branch rule are unchanged, since the comment does not clearly lower them.

## Quality index

The reference has a different subject, so no direct comparison exists. `data/spikes/night/jill_index.py` is a scale chosen for this work, not an established measure of quality. It compares the top 15 % of the body, the mean of four ratios against the reference, each capped at 100:

- `head_faces`: faces in the head region.
- `head_density`: texture pixels per body height.
- `texture_sharpness`: mean Laplacian of the textured head render, with smooth shading.
- `shape_sharpness`: the same on the untextured render.

A result counts only if its head fidelity to the input image (`score.py`, DINOv2) is at least 0.60, since noise also raises sharpness. The separate eyes, teeth, and hair of the reference are not measured. The user's verdict on the renders decides approval.

## Context

`docs/userstories/staged-factory/README.md` holds the measurements this builds on. The reference's face has 12 to 30 times our texture density, Hunyuan halves its texture on save, and the test image gives the head about 180 px of width. Code in `data/spikes/` is not versioned; only a change worth keeping goes to a branch.

## US-01: Know what hurt the face

As a user, I want the geometry-then-paint flow tested one change at a time, so that I know which change lowered the head score.

- US-01 / AC-01: Paint runs with the same seed differ in the image upscale only, one per card at the same time. Each output has 4096 px textures. Scores and renders are recorded here.

## US-02: A head with its own texture

As a user, I want the head painted on its own from a head crop into its own texture, so that the face gets more of the texture and of the source image.

- US-02 / AC-01: The output GLB has a body material and a head material, each 4096 px. Density per material is measured with the same method as the reference.
- US-02 / AC-02: Head score, head render, and the neck seam are recorded next to the body-only result and Jill.

Dependencies: US-01.

## US-03: Keep what works

As a user, I want a change that clearly improves the model on a git branch, so that I can review it in the morning.

- US-03 / AC-01: A change goes to a branch only if its renders and scores beat the current result. Engine tests pass on that branch. If nothing qualifies, no branch is made, and the reason is recorded here.

Dependencies: US-01, US-02.

## Results

Both runs: seed 1234, 500 000 faces, CuMesh UVs, 4096 px textures, one per card, 324 s in total.

| Run | Index | Faces | Density | Texture sharpness | Shape sharpness | Head fidelity | Full-body fidelity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Jill | 100 | 100 | 100 | 100 | 100 | n/a | n/a |
| `A_x1`, original image | 51.0 | 100 | 11.6 | 56.2 | 36.2 | 0.626 | 0.787 |
| `B_x4`, upscaled ×4 | 52.1 (fails the 0.60 gate) | 100 | 11.8 | 59.0 | 37.4 | 0.534 | 0.785 |

The ×4 upscale is what hurt the face: head fidelity drops from 0.626 to 0.534 with everything else equal. Texture density is the largest gap to the reference.

Correction to the index: the first baseline measured the top of Jill's raised rifle, not her head. The baseline now comes from `jill_no_rifle.glb`. Numbers below use it; the table above does not.

| Run (seed 7, base `S7_x1`) | Index | Faces | Density | Texture sharpness | Shape sharpness | Head fidelity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `S7_x1`, whole-body paint | 54.2 | 23.6 | 10.8 | 86.0 | 96.2 | 0.607 |
| `H_S7_crop`, head painted alone from a crop | 63.5, fails the gate | 23.7 | 33.8 | 100 | 96.5 | 0.263 |
| `P2_S7`, image pixels projected straight on | 63.5 | 23.7 | 33.8 | 100 | 96.5 | 0.728 |
| `P3_S7`, projected after a head pose search | 62.4 | 23.7 | 33.8 | 95.7 | 96.5 | 0.629 |

- Painting the head alone with Hunyuan loses the subject's colors (white ears, tan face). Rejected.
- Projecting the image's pixels gives real fur and whiskers. Hunyuan paint is used for what the camera cannot see.
- The pose search matters: TRELLIS.2's head is about 21° yaw and 7° pitch off the image. By eye `P3_S7` is clearly the best face, with eyes and nose in place. Its fidelity is lower than `P2_S7` because the front render now shows a truly turned head; the score does not reward this, and `P2_S7` looks broken.

### Target 70 reached

Same seeds as the bodies, simplified to 3 M faces instead of 500 000, with only the head's faces kept from the dense mesh. "Turned" also rotates the head to the image's pose (about 18° yaw, 7° pitch).

| Run | Index | Faces | Density | Texture sharpness | Shape sharpness | Head fidelity | Full-body fidelity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `D_S7` | 72.4 | 59.7 | 30.1 | 100 | 99.9 | 0.622 | 0.796 |
| `D_S7_turned` | 72.6 | 59.1 | 31.3 | 100 | 100 | 0.722 | 0.804 |
| `D_A` | 71.0 | 60.2 | 27.8 | 98.6 | 97.6 | 0.630 | 0.783 |
| `D_A_turned` | 72.0 | 59.6 | 28.6 | 100 | 100 | 0.689 | 0.782 |

`D_S7_turned` is the best result. It has the highest head fidelity except `P2_S7`, whose face is visibly wrong, and it comes close to Hunyuan's full-body 0.832. By eye the face is a recognisable match to the image, with eyes, nose, and fur from the image's own pixels. Open flaws: the ears still look faceted and dark, and turning the head opens a small gap at the neck.

The index reads high on sharpness because `head_faces` and sharpness are easy to satisfy. Density (31) is what stays far from the reference, and the reference's separate eyes, teeth, and hair are not measured. 72.6 is a position on this scale, not 72.6 % of the reference's quality.

Generated models are in `data/spikes/night/models/` (not versioned). Branch `feature/head-detail-70` holds the tools in `scripts/head_detail/`. They need `MODEL3D_DETAIL_IMAGE` and `MODEL3D_DETAIL_RUNS` set. They were checked with `py_compile` only after the move; the runs above used the copies in `data/spikes/`. The engine is not changed, so its tests do not apply.

### Targets 75 and 80 reached on the scale, with no visible gain

Same pipeline with 6 M faces: `E_S7_turned` scores index 82.5 (faces 100, density 30.2, sharpness 100 and 100), head fidelity 0.721, full body 0.804. `E_S42_turned` scores 75.0, head fidelity 0.638. Branches `feature/head-detail-75` and `feature/head-detail-80` exist under the agreed rule, at the same commit.

This is not a real approval. Nearly all of the gain from 72.6 comes from `head_faces` saturating, head fidelity did not move, and the renders of `D_S7_turned` and `E_S7_turned` look alike. The scale has run out: three of its four parts are at 100. The user's verdict decides.

Shards: dark triangle patches appear on faces and armor. Cause: `trellis_shape.py` simplifies without the cleanup `to_glb` runs after it, ending in `unify_face_orientations`. Tried and rejected: smooth normals alone (`G_S7`, more shards) and repairing winding with trimesh (`W_S7`, 46 % of faces flipped, broken; `X_S7`, did not converge). The cleanup in `unwrap_cumesh.py` fixed the shards on the armor (`C_S42_turned`, head fidelity 0.765). `C_S7` came out with scrambled body colors; the cause was not found, and that seed was dropped.

### The honest numbers: clean models

The remaining facets on the face and ears were lighting. Averaging normals across neighbours removed them (`N_S42`), and the index fell from 74.7 to 67.6 with the same textures. Facets had been inflating the sharpness parts. So 72.6 and 82.5 above are too high, and those results are not comparable with the table below, which is the one to trust. Branches `feature/head-detail-75` and `-80` were created from those inflated numbers and did not earn their names.

All with 6 M-face heads, cleanup, smoothed normals, projection after pose search, and the head turned to the image:

| Model | Index | Faces | Density | Texture sharpness | Shape sharpness | Head fidelity | Full-body fidelity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `N_S11_turned` | 74.7 | 100 | 34.2 | 92.9 | 71.5 | 0.769 | 0.753 |
| `N_S3_turned` | 71.4 | 99.5 | 31.8 | 84.3 | 70.0 | 0.756 | 0.762 |
| `N_S42_turned` | 67.6 | 95.5 | 33.0 | 78.8 | 63.2 | 0.765 | 0.799 |

For reference, before this work: Hunyuan `high` had head 0.651 and full body 0.832; TRELLIS.2 alone had head 0.676 and full body 0.777.

Results:

- Target 70 is met by two of three clean seeds; 75 and 80 are not. Branch `feature/head-detail-clean-70` holds this code.
- All three seeds exceed the earlier best head fidelity of 0.676, so the gain repeats.
- Full-body fidelity is below Hunyuan's 0.832 on all three, and it varies by seed.

Open flaws:

- A blue or pale arc under the chin. The cause first written here, collar pixels projected onto the jaw, was wrong; see US-04.
- The face is the image's pixels with the image's lighting, and only from the front. The sides and back of the head are still the lower-detail paint.
- The body has no projection and keeps the shared 4096 px texture.
- Eyes, teeth, and hair are not separate pieces, as they are on the reference.
- The tools run with paths and head-selection values tuned to this one test image. They are not ready for the engine or for other images.

## US-04: No arc under the chin

Approval (verbatim): "Sí, corregilo".

As a user, I want the arc under the chin gone, so that the armor's inside does not show below the face.

- US-04 / AC-01: On the three clean models, the front head render shows no pale or blue arc under the chin.
- US-04 / AC-02: Index and head fidelity stay within 1 point and 0.01 of the clean-model table.

Cause: rendering the pieces apart shows the head's chin is clean. The arc belongs to the body, seen through the hole left when the head's faces are cut out of it. On `N_S42_turned` the same hole also shows as a blue band across the mouth.

Fix: `data/spikes/night/neck_cap.py` closes the body's largest open loop with a near-black fan with a single normal. The image's collar interior is near black too. `project_head.py` calls it for new runs. Models: `N_S11_capped.glb`, `N_S3_capped.glb`, `N_S42_capped.glb`.

| Model | Index before | Index now | Head fidelity before | Head fidelity now | Full-body fidelity now |
| --- | ---: | ---: | ---: | ---: | ---: |
| `N_S11_capped` | 74.7 | 73.9 | 0.769 | 0.763 | 0.752 |
| `N_S3_capped` | 71.4 | 71.2 | 0.756 | 0.759 | 0.775 |
| `N_S42_capped` | 67.6 | 67.7 | 0.765 | 0.760 | 0.799 |

Open flaws seen during this work:

- The cap reads as a flat dark patch with a jagged edge when brightened; it has no fur.
- On `N_S42_capped` a dark band stays across the mouth: the blue became dark, but the gap remains.
- The side view shows blue armor paint on the sides of the head.
- The head piece holds stray shards from the shoulder straps, which turn with the head.

## US-05: No armor blue on the head's sides

Approval (verbatim): "Sí, los costados".

As a user, I want the sides of the head free of armor blue, so that the head looks like fur from the side.

- US-05 / AC-01: The side view of the head shows no armor blue on the three clean models.
- US-05 / AC-02: Index and head fidelity stay within 1 point and 0.01 of US-04's table.

Cause: Hunyuan's whole-body paint itself gives the head's sides and back armor colors, strongly on seeds 11 and 42. The projection only falls back to that paint.

Fix: `data/spikes/night/armor_fill.py` replaces paint with blue minus red above 12 by the nearest fur color, plus two rings around it, then blends. It runs after the pose search and touches only the fallback. It replaced 36.5 %, 18.5 %, and 52 % of the head's vertices on seeds 11, 3, and 42. Models: `N_S11_sides_turned.glb`, `N_S3_sides_turned.glb`, `N_S42_sides_turned.glb`.

| Model | Index now | Head fidelity now | Full-body fidelity now |
| --- | ---: | ---: | ---: |
| `N_S11_sides_turned` | 73.9 | 0.762 | 0.752 |
| `N_S3_sides_turned` | 71.1 | 0.756 | 0.775 |
| `N_S42_sides_turned` | 67.5 | 0.761 | 0.800 |

Open flaws seen during this work:

- The filled sides are smeared gray patches without fur detail.
- A pale strap stripe stays on the side of `N_S11_sides_turned`; it is not blue, so the test does not catch it.
- User's comment (verbatim): "Hay algo raro con la cabeza  es como si no fuera parte del cuerpo". The head is a separate mesh, cut from the body and turned up to 21°, with a sharper texture than the body and no join at the neck.
- Turning the head causes most of that: `N_S11_sides.glb`, the same textures with the head left in its seat, shows no neck gap or cover by eye, though its head fidelity is 0.663 against 0.762, and its full-body fidelity 0.737 against 0.752. The score compares against an image whose head has another pose, so it penalises this version. Comparison: `data/spikes/night/models/N_S11_turned_vs_seated.png`.

## Decision: the head stays in its seat

User's decision (verbatim): "Vamos a ver  lo mejor es la cabeza en su lugar no importa si el parecido no es el 100% lo mejor es tener un modelo funcional".

The main models are now the ones with the head in its seat, `N_S*_sides.glb`. The `_turned` files stay for comparison. This changes what is measured: the seated numbers below replace the turned ones as the current result.

| Model | Index | Head fidelity | Full-body fidelity | Paint replaced on the head |
| --- | ---: | ---: | ---: | ---: |
| `N_S11_sides` | 70.5 | 0.663 | 0.737 | 36.5 % |
| `N_S3_sides` (chosen) | 69.3 | 0.718 | 0.770 | 18.5 % |
| `N_S42_sides` | 63.7 | 0.715 | 0.797 | 52 % |

`N_S3_sides` is the chosen model: best head fidelity, least repainted head, and no strap stripe or band across the mouth. Its index is under 70, and only `N_S11_sides` reaches it. All views: `data/spikes/night/models/seated_all_views.png`.

The head, body, and cover are still three separate open meshes, and the lowest tenth of the head sits a median of 0.02 to 0.04 from the body on a model 2 units tall. None of this runs in the engine.

### Rig and walk test

Approval (verbatim): "Sí, probalo".

`N_S3_sides.glb` went through the engine's `unirig_job.py` (2 min 58 s on one card) and the client's `animate_rig.py`, run by hand, not through the API or the app.

- Rig: passed. Detected as a biped with 2 legs and 2 arms. All three pieces carry skin weights.
- Walk: passed. Head and body do not part during the walk: 0 change at the neck over frames 0 to 18. The walk only keys legs, arms, and root, so this says little about the neck itself.
- Head turned 30° by hand: tore the face and rippled the collar and straps.

Cause of the tearing: UniRig's weights come in steps (median edge jump 0, 99th percentile 0.074), and on a dense mesh a small step moves neighbours further apart than their edge length. UV chart copies were not the cause: their weights are equal.

Fix tried: `data/spikes/night/smooth_weights.py` averages the weights over the welded surface, 60 passes, in about 7 s. The 99th percentile falls to 0.013, and the face stays whole on the same turn. Neck parting on the turn: max 0.023 before and after, on a model 2 units tall. Files: `N_S3_rigged.glb`, `N_S3_walk.glb`, `N_S3_walk.gif`, `N_S3_rig_before_after.png` in `data/spikes/night/models/`.

Not fixed or not checked:

- The turn drags and stretches the collar, since it shares weights with the head.
- The chainsaw and rifle bend with the arms and legs, as they are one mesh with the body.
- The smoothing moved to the engine; see `docs/userstories/skin-weight-smoothing/README.md`.
- FBX, OBJ, STL, and USDZ export, and loading in the app, were not tried.

### The face does not pass by eye

User's comment (verbatim): "UFF ESA CARA ES UN MOUNSTRUO".

Comparison with the image, unbrightened: `data/spikes/night/models/faces_vs_image.png`. Seen on all seeds, in both poses:

- The generated skull is wider than the dog's, and the features sit small and low in it. The image's pixels stretch to cover it.
- The eyes are painted on, and do not sit in sockets that match them. Dark, waxy patches surround them where the image's pixels fade into the whole-body paint.
- The low brows read as a human, angry expression, which the image does not have.
- The ears are glossy and have no fur.

The index and head fidelity do not catch any of this, and "whole" in the rig test only means not torn.

### Bending the head to the image

Approval (verbatim): "Sí, probalo".

The bare head shows the cause: it has a feline shape with lips, not this dog's. `data/spikes/night/warp_head.py` bends it so 17 hand-marked points (eyes, nose, mouth, chin, forehead, cheeks, ears) land where the image has them from the front, with the neck's cut edge fixed. Largest move: 0.039 on a model 2 units tall. The image then projects straight on, with no pose search. Points: `data/spikes/night/landmarks_s3.json`.

| Model | Change | Share of head texture from the image | Seen against the image |
| --- | --- | ---: | --- |
| `N_S3_sides` | None | 31 % | Angry expression, waxy patches |
| `W_S3` | Bend | 33 % | Expression and proportions closer; waxy patches stay; a pale collar stripe on the left cheek |
| `W2_S3` | Bend, and image pixels used on steeper surfaces (`FADE_FROM, FADE_TO` from 0.25, 0.60 to 0.05, 0.25) | 50 % | Fur over most of the face, reads as a dog; best so far |

Comparison: `data/spikes/night/models/W2_S3_vs_image.png`.

Seen on `W2_S3`: a smooth dark patch around the left eye, the collar stripe on the left cheek, glossy ears without fur, and a skull still wider than the dog's at the top.

### Left eye and collar stripe

Approval (verbatim): "Sí, seguí".

The waxy patch and the glossy ears were mostly the check render's fault, not the model's. `render_head.py` uses Blender's Workbench, whose specular highlight ignores the material's roughness. The same model, flat lit or rendered with Cycles and its real materials, shows fur and matte ears. Earlier judgments of waxiness and gloss in this document came from those renders. Close-ups are now judged with a Cycles render.

The collar stripe was real: the head's edge took the image's armor pixels. `project_head.py` now replaces the image's armor, found by blue minus red above 15 in regions larger than an eye's glint, with the nearest fur before projecting (`armor_mask`, `fill_armor`).

Tried first and rejected: `W3_S3` sent those texels to the whole-body paint, which left a pale patch on the cheek.

Current best: `data/spikes/night/models/W4_S3.glb`. Comparison, with Cycles: `data/spikes/night/models/W4_S3_vs_image.png`.

Still wrong on `W4_S3`:

- A small smear on the lower left cheek, where the fill stretches fur.
- A skull still wider than the dog's at the top.
- Sides and back of the head with less detail than the face.
- Ears dark, without visible fur.

Limits: one seed and 17 hand-marked points, so this does not work on other images. The app's viewer was not checked. No score was used to judge this.

### `W4_S3` rigged and walking

Approval (verbatim): "Sí, ponele".

`W4_S3.glb` went through `unirig_job.py`, with the smoothing step, in 2 min 27 s, and `animate_rig.py`, run by hand. Results: `W4_S3_rigged.glb`, `W4_S3_walk.glb`, `W4_S3_walk.gif`, `W4_S3_motion.png` in `data/spikes/night/models/`, rendered with Cycles.

- Rig: passed. Biped, 2 legs and 2 arms.
- Walk: passed. No parting at the neck, since the walk does not key the neck.
- Head turned 30° each way by hand: the face stays whole. Neck parting at most 0.016 on a model 2 units tall.

Problems the turn shows, none visible at rest:

- Turned to the left, the left cheek shows brown streaks and shards, from the fill and from the mesh's edge.
- The head's lower edge is jagged and opens a dark gap at the neck.
- The shoulder straps bulge, since the head's bone reaches them.

The chainsaw and rifle still bend during the walk.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-05 / AC-01 | Side renders of the three models | Passed by eye: no armor blue; gray patches and one strap stripe remain |
| US-05 / AC-02 | `jill_index.py`, `score.py` | Passed: largest changes are 0.2 index points and 0.003 head fidelity |
| US-04 / AC-01 | Front renders of the three capped models, compared with the turned ones | Passed by eye: arc gone on all three |
| US-04 / AC-02 | `jill_index.py`, `score.py` | Passed: largest changes are 0.8 index points and 0.006 head fidelity |
| US-01 / AC-01 | `score.py`, renders, texture size | Passed: 4096 px; ×4 upscale lowers head fidelity from 0.626 to 0.534 |
| US-02 / AC-01 | `jill_index.py` on the GLB | Passed: body and head materials, 4096 px each; density 34 of 100 |
| US-02 / AC-02 | `score.py`, renders | Passed: recorded above; the neck arc remains |
| US-03 / AC-01 | Index rule, as revised by the user | Partial: clean-70 is earned. 75 and 80 exist but are not earned. Engine unchanged, so its tests were not run. Moved scripts checked with `py_compile` only, not re-run from the branch |
