# Kit assembly

Status: approved 2026-11-08 ("Approved, all stories"), revised the same day ("Approve both, resume"). Branch: `exp/kit-assembly`. Execution mode: all stories.

## Revisions

- 2026-11-08: the app serves any subject, not only people: "no necesariamente voy hacer solo humanos o las personas que utilizan la aplicacion pueden ser hasta objectos". Human-only models (SMPL-X, FLAME, PSHuman) are out. A model whose skeleton has no head, arm, or leg passes through unchanged (US-01 / AC-03).
- 2026-11-08: textures follow the reference model (`/home/pumba/Downloads/jill-re3`: four 4096 px skin tiles for face, body, legs, arms, plus separate materials). Each part keeps its own 4096 px texture as its own material instead of one shared atlas (US-07 rewritten).

## Context

On 2026-11-08 the user reviewed the two latest TRELLIS.2 `high` models (a man and a dog-headed figure, jobs `01M6Y2QGM129CQTEJ6FNTEFWZ6` and `01M6Y333HA86SAQ88C4J7AZ2C2`). Bodies match their images; faces lose detail: eyes become slits, the man's smile is gone, and the shirt logo turns to noise. Cause: TRELLIS.2 crops the image to the subject and conditions on at most 1024 px, so a full-body figure's face gets about 100 px, and the 1024 voxel grid is spread over the whole body.

The user's idea, verbatim: "como persona logica pienso que es una analogia que podemos hacer para pulir los detalles ya que el limite es la memoria de mi pc , quiero decir de alguna manera un modelo entero ocupa muchas capas si me enfoque en 5 partes separadas los resultados serian mejores y luego se unen". They showed a sculpted miniature sold as a kit: head and torso, arms, and legs cut with flat planes at the joints and keyed together. Approval to try it: "Dale probemos". It must work for humans, anthropomorphic animals, and future characters ("Los 2 o cualquier modelo a futuro").

What earlier work established (`docs/userstories/staged-factory/`, `head-texture/`):

- Generating a part alone gives it the whole grid and more image pixels; the sculpted head gained a dog's muzzle and nose.
- Joining failed three ways: pasted parts overlap in layers that shred the paint; a voxel fuse needs closed volumes and the parts were open sheets; displacing the mold toward the parts left a spiky surface.
- ICP alignment of parts to the mold worked (error under 0.001).
- Hunyuan paint on TRELLIS.2 geometry lowered head fidelity (0.676 to 0.523-0.608), so this plan keeps TRELLIS.2's own textures.
- The earlier spike code in `data/spikes/` and the `feature/head-detail-*` branches no longer exist.

New in this plan: cut every part and the mold with a flat plane at each joint and cap the cut, so each piece is a closed solid. Closed solids are what the voxel fuse needed.

## Scope and assumptions

- An experiment, not an engine feature. Tools live in `scripts/kit_assembly/` on this branch, so they are versioned this time. Engine and app integration is a later, separately approved backlog, only if the result is better.
- Parts: head, torso, left arm, right arm, left leg, right leg (the six named on 2026-10-30; the user's "5 partes" is read as the same idea). A character whose skeleton lacks a part (no legs, for example) keeps that region in the torso.
- Test images: the two jobs above, with their seeds. Baseline: their current `model.glb`.
- Runtime: TRELLIS.2 steps run with `vendor/TRELLIS.2/.venv`, UniRig with its own venv, mesh steps with `vendor/blender/blender -b` or the engine venv. GPU steps run one at a time and release memory; no Model3D job and no local LLM may run on the GPUs at the same time.
- Out of scope: 3D printing, peg/socket keys, Hunyuan paint, human-only face models.

## US-01: Mold and skeleton

As a user, I want the whole body generated once with its skeleton, so that every part has a place and a joint to attach to.

- US-01 / AC-01: Given a test image and its seed, when the mold step runs, then `mold.glb` (TRELLIS.2 `1024_cascade`, textured) and `mold_rig.glb` (UniRig) are written to the run folder, and time and peak VRAM are logged.
- US-01 / AC-02: Given `mold_rig.glb`, when joints are read, then the head, arm, and leg chains are found by bone position and hierarchy (no bone names), with the joint where each meets the torso. A missing chain is logged, and its region stays in the torso.
- US-01 / AC-03: Given a model where no head, arm, or leg is found (an object, or no skeleton), when the run continues, then `model.glb` is the mold unchanged and the reason is logged.

Verification: run on both images; log and file listing.

## US-02: Part crops

As a user, I want each part cut from the original image at full resolution, so that the part model sees more real pixels than the whole-body run.

- US-02 / AC-01: Given the mold aligned to the image's front view, when crops are made, then each part's region of the original 2048 px image is saved as `crops/<part>.png` with its background removed, and the head crop is at least twice as many pixels wide as the head in the 1024 px whole-body input.
- US-02 / AC-02: A contact sheet `crops.png` shows the six crops for review.

Verification: crop sizes logged; contact sheet inspected.

## US-03: Sculpt each part

As a user, I want TRELLIS.2 to generate each part alone, so that each part gets the whole voxel grid.

- US-03 / AC-01: Given the six crops, when the sculpt step runs, then `parts/<part>.glb` is written for each, textured, generated one at a time with memory released between parts; time and peak VRAM per part are logged.
- US-03 / AC-02: A part that fails or runs out of memory is recorded, and the run continues with the mold's region for that part.

Verification: run log; per-part renders.

## US-04: Align parts to the mold

As a user, I want each part placed where it belongs on the body, so that the pieces meet at the joints.

- US-04 / AC-01: Given a part and its mold region, when alignment runs (scale, rotation, translation, then ICP), then the mean distance from the part's points to the mold region is logged, and a part above the threshold set by the first run is reported as misaligned.
- US-04 / AC-02: A render of the six aligned parts over the mold, in front and side views.

Verification: logged distances; render.

## US-05: Kit cut and cap

As a user, I want every piece cut flat at its joints and closed, so that the pieces meet face to face instead of overlapping.

- US-05 / AC-01: Given the joints and aligned parts, when the cut runs, then one plane per joint, perpendicular to its bone, cuts each part and the mold's torso, and each cut is capped.
- US-05 / AC-02: Every resulting piece is watertight (no boundary edges) and has a positive volume; a piece that is not is reported with its count of open edges.
- US-05 / AC-03: Neighbouring pieces share their cut plane; the gap or overlap along the plane is measured and logged.

Verification: watertightness and volume per piece; render of the exploded kit.

## US-06: Fuse into one surface

As a user, I want the pieces merged into one closed surface, so that the model has no layers or seams in its geometry.

- US-06 / AC-01: Given the closed pieces, when the voxel fuse runs, then `fused.glb` is one watertight mesh with no faces inside another piece, at a face count no higher than 500 000.
- US-06 / AC-02: Time and peak memory (RAM and VRAM) are logged; the step fits this machine (two 12 GB cards).

Verification: mesh checks; front, side, and head close-up renders.

## US-07: Transfer the paint

As a user, I want each part to keep its own texture on the fused surface, like the reference's face, body, arms, and legs tiles, so that the head gets a whole texture instead of a sliver of a shared one.

- US-07 / AC-01: Given `fused.glb` and the aligned textured parts, when the bake runs, then `model.glb` is one mesh with one material per piece (torso plus each sculpted part), each with its own UVs and a 4096 px base color texture baked from that piece's source surface (the part, or the mold for the torso).
- US-07 / AC-02: Inside a part's material, texels its part surface does not reach take their color from the mold, and their share per material is logged.
- US-07 / AC-03: Texture density of the head material, in pixels per body height, is measured with the method in `docs/userstories/staged-factory/` and compared with the current model and the reference.

Verification: renders; texel shares; density table; the GLB opens in Blender.

## US-08: Compare and decide

As a user, I want the kit model shown next to the current model and the image, so that I can decide whether it is worth bringing into the app.

- US-08 / AC-01: For both images, a sheet with the input, the current `model.glb`, and the kit `model.glb`, in front view and head close-up, rendered the same way.
- US-08 / AC-02: Total time per run against the current job's time, and the user's verdict, are recorded here.

Verification: comparison sheets; user verdict.

Dependencies: US-01 → US-02 → US-03 → US-04 → US-05 → US-06 → US-07 → US-08.

## US-09: One command for a head pass

Added 2026-11-08 at the user's request ("Yes, write the runner") after the results showed that only the head is worth sculpting.

As a user, I want one command that takes a finished job from the app and gives it a sculpted head, so that I can try it on new images without running eight tools by hand.

- US-09 / AC-01: Given a rigged job folder (or job id) in `data/jobs`, when `python scripts/kit_assembly/run.py JOB` runs, then it prepares `data/kit_runs/<job_id>/`, generates only the head-and-shoulders crop (the job's seed plus `--extra-seeds`), and runs align, kit, paint, and compare, ending with the paths of `model.glb` and the comparison sheets.
- US-09 / AC-02: When no engine answers, it starts one with TRELLIS.2 for the generation and stops it afterwards; an engine that was already running is used and left running.
- US-09 / AC-03: A step whose outputs exist is skipped, so a rerun resumes; a failing step stops the run with its name and the end of its output.
- US-09 / AC-04: A job without a skeleton ends with `model.glb` equal to the job's model and the reason, without generating anything.

Verification: a run on the man's job from a clean run folder; a rerun that skips every step; a run on a job with no skeleton.

## US-10: Replace only the head

Added 2026-11-08 after the alien and the cat came out with blurred shirts and noisy bodies, because the whole body was remeshed and re-baked. Approval (verbatim): "Yes, head only".

As a user, I want the head pass to keep the original body's mesh and texture and change only the head and neck, so that the face improves without the body getting worse.

- US-10 / AC-01: Given a sculpted head, when the pass runs, then `model.glb` holds the original mold's triangles and material for every face outside the head region, unchanged, plus one patch mesh (the sculpted head and a neck band blended into the mold) with its own 4096 px material.
- US-10 / AC-02: The patch overlaps the kept body by about 1 cm inside the neck, so no gap opens where they meet.
- US-10 / AC-03: When no sculpted head passes the volume check, `model.glb` is the mold unchanged.

Verification: the five run characters re-run; front and head renders against the app's model; the kept body faces' vertex positions equal the mold's.

## Log

- 2026-11-08: TRELLIS.2 meshes are hollow shells. After welding UV seams, the man's mold has 0 boundary edges and 522 non-manifold edges, and a ray through the chest crosses 4 walls (outer and inner, front and back); its enclosed volume is about a tenth of the body's. Ray-parity inside tests read the body as empty, so `sdf.py` defines inside by flood-filling the exterior on a grid and signs points near the outer wall by the closest face's normal.
- 2026-11-08: an exploration script that split the 147 000-face mesh into components with trimesh used 57 GB and was killed by the kernel. Tools now run under `ulimit -v`.
- 2026-11-08: `sdf.py` from `qwen/qwen3-coder-30b` (LM Studio) was rejected twice. The first answer looped over every grid node in Python and offset faces by block count; after one focused correction the block extractor was still wrong (blocks built per axis instead of as 3D combinations), the cutter used an intersection of capsules instead of a union, and the occupancy grid was not reshaped (`IndexError`). Tests in `tests/test_sdf.py` (same model) were accepted. Blocked on the implementer route.
- 2026-11-08: the user chose the hosted lightweight implementer ("Hosted lightweight model"), then authorized the planning agent to write the code: "Escribe tu el codigo de ahora en adelante". The hosted run was stopped before it changed anything. Parts are generated through the engine's HTTP API (`quality: high`, the job's seed, no rig), so no TRELLIS.2 driver is written; the mold and its rig are the existing jobs' `rig_input.glb` and `model.glb` (same pipeline and seed).

- 2026-11-08: marching cubes blocks only close up when vertices are welded by grid edge, not by rounded position: scikit-image returns float32 vertices, and Lewiner's method adds vertices inside ambiguous cubes, which are never shared. After that fix the man's mold extracts closed at h = 1/256, 1/512 and 1/1024 (195 000, 939 000 and 4.07 M faces; 1, 5 and 20 s).
- 2026-11-08: `animate_rig.py`'s arm rule (tip moves sideways more than 15 % of the height) misses hanging arms (13 % here); `rig_read.py` takes the head as the highest rising chain and the arms as its sibling chains, and cuts at the shoulder when the first arm bone is flatter than 45 degrees (a clavicle). UniRig skin weights bleed where parts touch (the dog's thigh went to the hand resting on it); labels are seeded where the dominant weight and the nearest bone agree and filled by geodesic distance over the surface.
- 2026-11-08: crops give the head 2.0 times the real pixels of the whole-body run on both images (US-02 / AC-01). Projection fit IoU: 0.83 (man), 0.74 (dog).
- 2026-11-08, first parts (man): the sculpted head is a real head with open eyes, blue irises, ears, and hair volume (the whole-body head has slit eyes). The sculpted torso is a flat sheet, a loose shirt with the logo mirrored on the back: a torso crop does not read as a body. Arms have real volume plus flat scraps of shirt and shorts. Generated parts are open where the body continues (neck, sleeves), so `MeshField(enclosure=0.9)` treats cells that see the mesh in 90 % of 64 directions as inside; 0.75 filled out to the grid edge.
- 2026-11-08: scaled ICP collapsed the arms to a third of their size (one-way distance 4 mm, reverse 70-110 mm). `align.py` now keeps the box fit, rigid ICP, or scaled ICP with the lowest two-way distance. Man: head 3 mm both ways (scaled); arms rigid, 9.5-22 mm forward and 20-27 mm reverse, because TRELLIS.2 bends the arm differently from the mold.
- 2026-11-08: `kit.py` replaces a sculpted part with the mold when its volume inside its cutter is under half the mold's (the flat torso: ratio 0.007).

- 2026-11-08, all 12 parts (2-4 min each through the engine, VRAM peaks 9.9 and 7.4 GB): with two-way alignment and the volume check, only the man's head is kept (ratio 0.69). Rejected: both torsos (0.01-0.02, flat sheets), the dog's head (0.01), all limbs (0.00-0.17; real volume but misaligned 10-25 mm because TRELLIS.2 bends them differently). Three more seeds of the dog's head (11, 22, 33) are also flat cards, so it is the crop, not the seed: a frontal illustrated dog head reads as a picture.
- 2026-11-08, man, kit model (head sculpted, the rest mold), fused at h = 1/512 and decimated to 500 000 faces, closed (0 boundary, 0 non-manifold edges), 190 s and 3 GB RAM; textures per piece 57 s. Head texture density 5 380 px per body height against 2 503 for the current model (2.1x); the other pieces stay near 2 100-2 800 because their source is the mold's own atlas. Seen in `compare_head.png`: the head has open eyes with blue irises and the input's brows and beard (the current head has slit eyes), but the fused surface looks faceted (flat shading of marching-cubes faces) and the neck seam is shredded: the generated neck is a hollow tube where the plane cuts it (outline IoU 0.009).
- 2026-11-08: bpy processes hang in interpreter teardown after writing their output (paint sat 85 min at 0 % CPU); the bpy tools now end with `os._exit`. `pkill -f` with a pattern that appears in its own command line kills the calling shell; kill by PID.
- 2026-11-08, user asked whether this makes sense. Answer given: the head pass shows value; the six-part kit does not (5 of 6 parts fall back to the mold on the man, 6 of 6 on the dog), at 6 extra generations per model. Proposed next: look at the man's comparison, probe a head-and-shoulders crop on the dog, then revise this backlog to a head-only pass for approval.

- 2026-11-08, user on the first comparison (verbatim): "La ultima imagen se ve genial la cara y el cuerpo solo el problema es conectar el cuello con el resto del cuerpo". Fix: `kit.py` raises each sculpted part's cut along its normal to the first plane where the part is solid in 90 % of its outline and its outline is at least half the mold's, and above the cut the field turns from the mold into the part over 3 cm (smoothstep). `paint.py` exports smooth normals. Man: cut raised 7.4 cm (neck base to jaw), outline IoU at the cut 0.88 (was 0.009), fused surface closed (931 534 faces, then 500 000), 243 s, 2.6 GB. Head material 98.7 % from the sculpted head, density 10 704 px per body height (4.3x the current model). By eye: the neck is continuous and the collar comes from the mold; a slightly lighter band remains on the neck.

- 2026-11-08, dog with a head-and-shoulders crop (`crops/head_bust.png`, down to 5 cm below the shoulder joints): all three seeds are real 3D busts with a dog's muzzle, ears, and the open-mouth smile (the four head-only crops were flat cards). Alignment 4-9 mm both ways, solid-volume ratio 0.94-1.0, cut not raised (the bust is solid at the neck), outline IoU 0.93, fused closed. The bust's brighter shirt showed as a band above the cut until `paint.py` blended the colors with the same smoothstep weight as the geometry (an emission bake of the height above the cut plane). Fused faces now take the mold's geodesic labels, and a sculpted part claims the faces inside its cutter: before, a leg cutter that also covered the tail took 173 000 faces and left the other leg 1 012.
- 2026-11-08, US-09 runner (`run.py`): a job without a skeleton ends in 7 s with `model.glb` byte-identical to the job's, and a rerun skips every step. On the man's job from a clean folder, with no engine running: it started the engine, generated two busts (job seed and 11, 340 s), stopped the engine, and finished in 648 s; head from `head_bust.glb` (solid-volume ratio 1.06). By eye: open blue eyes and a continuous neck, but a darker, heavier beard than the image, and the head-only crop's face (previous run, kept in `data/kit_runs/01M6Y2QGM129CQTEJ6FNTEFWZ6_fullkit/`) is closer to the image. The pick between candidates is by solid volume, not likeness.

- 2026-11-08, user on the dog's bust face (verbatim): "esta es la que espero". Three new rigged jobs from the app (alien, black cat, a second dog image) through `run.py`, 691-707 s each. Heads: the new dog matches that face (sharp eyes, fur, ears, smile; the app's head is blurred with melted ears); the cat gets green eyes with pupils (the app's are yellow slits); the alien gets its skull ridges, nose and mouth, but its eyes are matte and its thin neck seam drips. The alien's mold head reads about a tenth as solid as the dog's (volume ratio 11), so its mold is probably open there. Bodies: worse than the app's model on the alien and the cat, with a blurred, darkened shirt logo and a noisy surface, because `kit.py` remeshes and `paint.py` re-bakes the whole body even where nothing changed. The new dog's job-seed bust failed with CUDA out of memory (the runner went on with seed 11 and records the failure in `parts.json`); what else held the GPU then is not known.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-02 | Runs on both images; log, files, joints found | Pending |
| US-01 / AC-03 | Run on a GLB with no skeleton: output equals the mold, reason logged | Passed (identical SHA-256) |
| US-02 / AC-01..AC-02 | Crop sizes; contact sheet | Pending |
| US-03 / AC-01..AC-02 | Run log; per-part renders | Pending |
| US-04 / AC-01..AC-02 | Alignment distances; render | Pending |
| US-05 / AC-01..AC-03 | Watertightness, volume, plane gap; exploded render | Pending |
| US-05 / AC-02 | Pytest on a synthetic capsule: cut and cap gives a watertight piece | Pending |
| US-06 / AC-01..AC-02 | Mesh checks; memory log; renders | Pending |
| US-07 / AC-01..AC-03 | Renders; texel shares; density table; opens in Blender | Pending |
| US-08 / AC-01..AC-02 | Comparison sheets; user verdict | Pending |

| US-10 / AC-01..AC-03 | Five characters re-run; renders; kept faces equal the mold's | Pending |
| US-09 / AC-01..AC-04 | Clean run on the man's job; rerun; job with no skeleton | Passed: man 648 s with engine started and stopped; no-skeleton job byte-identical in 7 s; rerun skipped every step |

The pipeline is an experiment judged by renders, so most checks are reproducible manual procedures; the pure geometry step (cut and cap) gets an automated test.
