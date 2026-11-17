# Sharper body texture from the input image

Status: first version approved 2026-11-12 ("Approve, all stories"); revised the same day after the projection findings ("Yes, revise it"); the revised stories below await approval. Branch: `feat/public-api-voice`. Builds on `docs/userstories/detailed-face-general/` (evaluation set, head pass).

## Context

Request (verbatim, 2026-11-12): "Tengo una duda se puede hacer lo mismo copn todo el modelo es decir trellis2 generar el modelo , y luego se afinan los detalles algo como un escultor de estatuas de piedra es trellis2 crea el molde y luego viene alguien y lo esculpe". Of the three refinement levels proposed (sharper texture, fine relief from estimated normals, multi-view diffusion refinement), the owner chose the first: "1".

### Facts checked

- Regenerating body parts separately was tried in `docs/userstories/kit-assembly/` (2026-11-08): torso crops came out as flat sheets and limbs bent differently from the body (1 to 2.5 cm off); only the head-and-shoulders bust works. This backlog does not regenerate anything: the TRELLIS.2 model is the mold, and only its colors change.
- The models' base color textures are already 4096 px (checked on three runs). Their detail is limited by TRELLIS.2 reading the input image shrunk to 1024 px on its longer side; the input image the app keeps (`trellis_input.png`) is 2048 px, twice the linear resolution.
- The current model-to-image projection (`crops.py`: a front orthographic fit of the model's silhouette to the image's alpha) matches only 67 % to 83 % of the silhouettes (IoU on three runs).
- `Trellis2TexturingPipeline` (`vendor/TRELLIS.2/trellis2/pipelines/trellis2_texturing.py`): it normalizes the mesh to its own bounding box, encodes its shape, samples a texture latent conditioned on the image (resized to 1024 px, cropped to its alpha box), decodes a PBR voxel field at 512 or 1024 cells over that box, and samples it at every texel of the mesh's UVs; with no UVs it unwraps new ones. A region of the model (a torso about a third of the body's height) therefore gets about three times finer voxels than the whole body.
- Its weights are in `data/hf` except the shape encoder: `shape_enc_next_dc_f16c32_fp16.safetensors` is missing (only its `.json` is cached). Using the pipeline needs that download.

### Scope

- In: the whole model's base color (front, sides and back, as the texturing model paints them), region by region, each from its crop of the input image, on the model's own shape and UVs.
- Out: geometry (no relief or displacement), regenerating parts, ML models other than TRELLIS.2's own texturing weights.
- Assumption to confirm with the backlog: in the app this is a separate option ("Sharper texture") on new generations, independent of rigging and of Detailed face, with the same notice when it keeps the original.

## Findings (2026-11-12)

Prototype `scripts/kit_assembly/texture.py` (with `glb_image.py`, which swaps a GLB's texture without touching meshes or skeletons, checked on a rigged model) on the man (`01M6Y2QG…`), front views splatted from the texture:

1. Projecting the 2048 px image's fine detail (finer than 4 px) after an affine silhouette fit plus dense optical flow on the texture: the shirt logo became legible ("TEMPOLIFT", the dial and the pulse line) where TRELLIS.2 had painted a green smear, but the logo kept TRELLIS.2's wrong green, seams waved, and the face got ghost lines (TRELLIS.2 turned the head; the picture faces front).
2. Trusting only regions where the view and the registered image correlate locally: the logo was dropped, since a wrong texture never correlates with the right one.
3. Registering the silhouettes' outline distance instead of the texture: the outlines still differ by about 30 px after the affine fit (pose and proportions differ), and the result was worse (correlation -0.14, 6 % trusted).

Root cause: TRELLIS.2's shape is not the picture's shape (pose of the arms, a turned head, proportions; silhouette IoU 0.67 to 0.83), so any projection of the picture onto the model needs a registration that the picture alone cannot give reliably.

Alternative found in `vendor/TRELLIS.2`: `Trellis2TexturingPipeline` (`example_texturing.py`) generates a PBR texture for a given mesh from an image. It also shrinks the image to 1024 px, but run per region (a region of the model with the matching crop of the picture, as the head pass does with the bust) it would paint on the model's own shape, with no registration.

## US-01: An evaluation set for texture sharpness

As the owner, I want texture changes measured on fixed characters, so that a sharper texture never comes with smeared or misplaced details.

- US-01 / AC-01: A script runs on the runs in `data/kit_runs/` (the 9 head-pass characters plus `01M78H5K…`) and writes, per character, the model's front view (its visible texels, as `texture.py` splats them) before and after, with close-ups of the face, the chest and a hand, next to the input image.
- US-01 / AC-02: It reports per character the sharpness of the front view (mean squared Laplacian inside the silhouette) relative to the input image's.
- US-01 / AC-03: The baseline numbers and sheet for the current models are recorded here before any change; the owner reviews the sheet.

## US-02: Texture a mesh with TRELLIS.2 through the engine

As a user, I want the engine to texture an existing model from an image, so that a model's colors can be redone on its own shape.

- US-02 / AC-01: With the owner's approval, the missing shape encoder is downloaded into `data/hf` (its size stated when asking); the engine then works offline as before.
- US-02 / AC-02: The TRELLIS.2 worker takes a texture request (a mesh with UVs, an image, a seed) and returns the base color written into that mesh's own UVs, at 512 or 1024 cells; the time and the peak VRAM on one 12 GB card are recorded here for both.
- US-02 / AC-03: A request that runs out of memory or fails ends as a job error with the reason; the worker stays usable for the next job.

## US-03: Texture the model region by region

As a user, I want each part of my character painted from its own close-up of my image, so that clothes, logos, skin and faces come out as sharp as the picture allows.

- US-03 / AC-01: A rigged model is split by its skeleton's parts (head, torso, each arm, each leg, as the head pass reads them); each region is textured with its crop of the input image (`crops.py`) and written into the model's texture where the region's texels are. A model without a skeleton is textured whole.
- US-03 / AC-02: Across region borders the colors blend over a few centimetres, so renders from the front, the sides and three-quarter views show no seam or color jump.
- US-03 / AC-03: On the evaluation set, sharpness rises for every character and the owner marks the result better or equal for every character; a region whose texture comes out worse than the model's (measured as in US-01) keeps the model's.

## US-04: Use it from the app

As a user, I want to ask for a sharper texture when I generate, so that I get it without running scripts.

- US-04 / AC-01: The generation panel has a "Sharper texture" option (Spanish catalog included); with it, a finished generation runs the region texturing and keeps the result as the model, with the previous one kept as `model_original.glb`.
- US-04 / AC-02: When the pass keeps the original (every region worse, or an error), the job succeeds with the model unchanged and the app shows the reason, as it does for Detailed face.
- US-04 / AC-03: Engine and client tests cover the option, the applied path and the skipped path with stub passes.

## US-01 results (2026-11-12)

`scripts/kit_assembly/evaluate_texture.py OUT [--after NAME]` reads the 12 run folders with an input image and a skeleton (read-only). Sharpness is the front view's mean squared Laplacian inside the silhouettes' overlap, divided by the input image's: 1.0 would be as sharp as the picture. The silhouette fit draws the model's triangles; the IoU only tells how much the model's outline overlaps the picture's.

| Run | Character | Silhouette IoU | Sharpness vs input (baseline) |
| --- | --- | --- | --- |
| `01M6Y2QGM129CQTEJ6FNTEFWZ6` | man | 0.72 | 0.25 |
| `01M6Y333HA86SAQ88C4J7AZ2C2` | dog | 0.64 | 0.05 |
| `01M6YJ432XJVQNQXYW32JD6XQT` | alien | 0.60 | 0.27 |
| `01M6YJCR9ATH3EFHNH4HBVSFKV` | cat | 0.52 | 0.15 |
| `01M6YK3NW2J2FVF3M4F1MZ57K7` | dog | 0.58 | 0.08 |
| `01M6YT0WEV02XG6KCDT2GHRZ6W` | dog | 0.63 | 0.08 |
| `01M6YVXTPCK9RW4Y4V88EEQQAE` | dog | 0.62 | 0.08 |
| `01M6YX342JQ2J62V5Q9ECZ01RD` | wolf | 0.50 | 0.07 |
| `01M714F2Y1091DTWTQBRZMWP9F` | (no sculpted head) | 0.61 | 0.21 |
| `01M76M50RHVREFWA9GEEN51C5B` | armoured | 0.61 | 0.21 |
| `01M78G4KCN1EZJY2CPS2HJHP1X` | new, from the app | 0.63 | 0.28 |
| `01M78H5KZZSW2B764KJNH7GET7` | new, from the app | 0.45 | 0.04 |

Every model holds 4 % to 28 % of the picture's fine detail in front view.

## Region texturing on the best model (2026-11-12)

`scripts/kit_assembly/retexture.py` (regions.py, texture_trellis.py) on the man generated at `1536_cascade`, 1 000 000 faces, 8192 px, rigged by UniRig: six regions (head, torso, both arms, both legs), each textured by TRELLIS.2's texturing pipeline from its crop at 1024 cells, written into the model's own 8192 px UVs and blended over 3 cm at the borders.

- Sharpness against the picture: 0.23 (TRELLIS.2's own texture) to 0.35.
- Face: blue eyes, a real beard, eyebrows and skin, close to the picture; the logo gains yellow lettering and the pulse line but is still not legible.
- Defects: a yellowish band on the neck and a purple tint on the left shoulder edge (region borders), a smeared thumbnail.
- Cost: 16 min on one card (about 2 min per region at 8192 px, 30 s loading); peak 11.3 GB on the head region, near the card's limit.

## US-05: High quality at 1536 across two cards

Added 2026-11-12. Owner: "prueba el 1536 con las dos tarjetas , tengo 82 gb de memoria ram", then, after the result, "Yes, use 1536".

As a user, I want "high" quality to use TRELLIS.2's highest resolution when my two cards share the work, so that every model starts sharper.

- US-05 / AC-01: With the engine split across two cards (generation on one, decoding on the other), a "high" job asks TRELLIS.2 for `1536_cascade`; "final" stays `1024_cascade` and "draft" `512`.
- US-05 / AC-02: On one card, where 1536 was never measured, "high" stays `1024_cascade`.
- US-05 / AC-03 (added 2026-11-12, owner: "PUES SACA LA MEJOR CALIDAD CARAJOOOOOOOOOOO"): split across two cards, a "high" job keeps 500 000 faces instead of 150 000; a job's own `target_faces` still wins. Raised the same day to 1 000 000 faces and an 8192 px texture (owner: "1M faces, 8192 px"), once the tiled bake of `docs/userstories/gpu-memory-pool/` made them fit.

Measurement (2026-11-12, the man's image and seed, the engine's own worker, two RTX 5070 of 12 GB): `1536_cascade` took 95 s against 63 s for `1024_cascade`, peaked at 5.3 GB on the generating card and 6.6 GB on the decoding card (`gpu/modes.py` budgets 5.2 and 10.1 GB for 1024), and its front-view sharpness against the picture was 0.35 against 0.25. The face got open eyes and clearer features; the logo is still not legible.

Best quality on two 12 GB cards (same image and seed, 2026-11-12):

| `1536_cascade` with | Result | Time | Peak, generating / decoding card |
| --- | --- | --- | --- |
| 150 000 faces, 4096 px | done, 140 035 faces | 95 s | 5.3 / 6.6 GB |
| 500 000 faces, 4096 px | done, 485 875 faces; the beard, eyelids and lips keep their relief | 112 s | 5.6 / 6.6 GB |
| 300 000 faces, 4096 px | done, 277 245 faces | 106 s | 5.6 / 6.6 GB |
| 500 000 faces, 8192 px | out of memory on the decoding card while baking | - | 5.6 / 11.1 GB |
| 1 000 000 faces, 8192 px | out of memory on the decoding card while baking | - | 5.6 / 11.1 GB |

UniRig rigged the 485 875-face model (textured meshes are not decimated before rigging) in 122 s with a 5.5 GB peak, keeping every face.

## Dependencies

US-01 → US-03; US-02 → US-03 → US-04. US-01 and US-02 are independent.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-02 | `evaluate_texture.py` on the 12 runs | Passed: table above, close-up sheet per run |
| US-01 / AC-03 | baseline recorded; owner review of the sheet | Baseline recorded; owner review pending |
| US-02 / AC-01..AC-03 | download with approval; GPU run on one model at 512 and 1024 (time, VRAM); worker test with a stub pipeline for success and failure | Pending |
| US-03 / AC-01..AC-03 | unit test: region texels and border blend on a synthetic mesh; evaluation set; side and three-quarter renders; owner review | Pending |
| US-04 / AC-01..AC-03 | engine and client tests with stub passes; a run in the app | Pending |
| US-05 / AC-01..AC-03 | `engine/tests/test_trellis_stage.py::test_high_quality_uses_1536_only_across_two_cards`, `::test_high_quality_keeps_500k_faces_across_two_cards`; GPU and UniRig measurements above | Passed (engine suite 409 passed); a run in the app pending |
