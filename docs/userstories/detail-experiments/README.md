# Detail experiments

Status: approved 2026-10-30, all stories, stop at US-04 for the user's choice.

Approval and clarified goal (verbatim): "El modelo de Jill es lo que quiero lograr conseguir si ves en la imagen tiene cara y todo lo demas , cuando utilizo una imagen como base quiero lograr generar un modelo a nivel de Jill seria como esculpir un modelo es decir algo muy matematico pienso yo es por eso que digo intentemos alcanzar ese nivel de detalle puedo dejarte trabajando toda la noche si asi lo amerita te parece?"

Planner's note given to the user: the Resident Evil 3 face was photo-scanned from a real model and then sculpted. A single image leaves the back, sides, and depth to be inferred, so Jill's level likely needs several views of the same character. That is a follow-up once these single-image results are in.

## Context

On 2026-10-30 the user shared a reference model (`/home/pumba/Downloads/jill-re3`, Jill from Resident Evil 3, a Capcom game asset used only as a visual reference, never redistributed or shipped) as "el nivel que quiero alcanzar". They then gave a test image (a dog-headed space marine with a bolter and a chainsword, on a battlefield background) and asked: "haz pruebas intercambiado pasos en modelos hasta lograr un 90% del detalle del modelo de la carpeta suministrada como base".

What the reference is (inspected with bpy):

- One mesh of 676 013 faces with 35 materials: body, face, lips, teeth, head, ears, eyes, hair, and a separate rifle.
- 4096 px textures: three body tiles and two rifle textures. Further 2048 px textures for the eyes and mouth.
- Hair made of alpha-tested cards. Hand-made by a AAA studio.

What our pipelines produce: one fused mesh, one 4096 px baked texture, 40 000 or 150 000 faces, lighting baked into the albedo. Separate eyes, teeth, hair cards, and per-part materials are beyond any open single-image generator known to the planner. So "90 % of the reference" cannot be measured directly (different subject, different kind of asset). These stories measure what can be measured and push our pipeline as far as it goes.

What exists: Hunyuan3D 2.1 (`high`: octree 512, paint at 768 px) and TRELLIS.2 (`512`, `1024_cascade`, with the two-card split), both on this machine. `data/spikes/` holds the render and stage scripts. TRELLIS.2's `1536_cascade` uses the 1024 weights already downloaded, so it needs no new download.

Assumptions to confirm when approving:

- The metric is fidelity to the input image. Each model is rendered from the front with its texture, against the background-removed input. The score is SSIM and a perceptual distance, computed over the full body and over a head crop. This ranks the variants; it is not a percentage of the reference model.
- No new model downloads. A variant that would need one is proposed separately.
- One seed per variant, plus a second seed for the top two variants to check the ranking holds.
- The winning variant becomes a new quality preset only after the user approves it (US-04).

## US-01: A repeatable fidelity score

As a user, I want each generated model scored against my input image, so that variants can be compared with numbers and not only by eye.

- US-01 / AC-01: A script in `data/spikes/` renders a GLB from the front (orthographic, textured, transparent background), aligns it to the background-removed input by the silhouette's bounding box, and prints SSIM and a perceptual distance for the full body and for a head crop. The head crop is the top 20 % of the silhouette.
- US-01 / AC-02: Scoring the input against itself gives SSIM 1.0 and distance 0. Scoring an existing Hunyuan model of this image gives a lower value. Both results are recorded here.

Verification: script output recorded.

## US-02: Run the variant matrix on the test image

As a user, I want the same image run through different combinations of models and settings, so that I can see which gives the most detail.

Variants (each with a fixed seed):

| ID | Shape | Texture | Settings |
| --- | --- | --- | --- |
| V1 | Hunyuan `high` | Hunyuan paint | current preset (baseline) |
| V2 | TRELLIS.2 `1024_cascade` | TRELLIS.2 | 150 000 faces, 4096 px (current `high`) |
| V3 | TRELLIS.2 `1024_cascade` | TRELLIS.2 | 500 000 faces, 8192 px |
| V4 | TRELLIS.2 `1536_cascade` | TRELLIS.2 | 500 000 faces, 8192 px |
| V5 | TRELLIS.2 (best of V3/V4 geometry) | Hunyuan paint `high` | hybrid |
| V6 | TRELLIS.2 (best geometry) | TRELLIS.2 texturing pipeline, re-run at 1024 | texture re-pass |

- US-02 / AC-01: Each variant's score (US-01), peak VRAM per card, time, face count, and texture size are recorded here. A variant that runs out of memory is recorded as such, not dropped silently.
- US-02 / AC-02: Head and full-body renders of every variant are sent to the user side by side, next to the reference render.

Dependencies: US-01. Verification: recorded table and renders.

## US-03: Head detail pass

As a user, I want the face generated on its own at full resolution and put back on the body, so that the face gets more detail than a whole-body run can give it.

- US-03 / AC-01: The head region of the input is cropped (from the silhouette top, about 20 %), generated alone with the best TRELLIS.2 setting from US-02, and placed on the best body: the body's head region is replaced at matching position and scale, and the seam is welded.
- US-03 / AC-02: The merged model is scored and rendered like US-02. If the seam or alignment is visibly broken, the result and the reason are recorded, and the story stops for a decision.

Dependencies: US-02. Verification: score and renders.

## US-04: Adopt the winner

As a user, I want the best variant available in the app, so that I can generate at that level from now on.

- US-04 / AC-01: The user picks a variant from US-02/US-03 results. It becomes a quality level, or replaces `high`, as the user decides, with its VRAM need checked against the run-mode thresholds.
- US-04 / AC-02: Engine tests pass and one job through the app produces the chosen variant.

Dependencies: user choice after US-02/US-03. Verification: tests and one app job.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | Score script output | Pending |
| US-01 / AC-02 | Self-score and baseline score | Pending |
| US-02 / AC-01 | Variant table | Pending |
| US-02 / AC-02 | Renders sent | Pending |
| US-03 / AC-01..AC-02 | Merged head score and renders | Pending |
| US-04 / AC-01..AC-02 | Engine tests and one app job | Pending |
