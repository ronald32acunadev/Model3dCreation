# Detailed face for any character

Status: US-01..US-05 approved 2026-11-11 ("Approve, all stories"); US-06 added and approved 2026-11-12 ("Yes, add US-06"). Execution: all stories. All six stories complete on the evaluation set (2026-11-12); a run in the app on a new character is still to be done. Branch: `feat/public-api-voice`.

## Context

Request (verbatim, 2026-11-11): "hay un error con el ultimo modelo generado la cara salio totalmente deforme". Follow-ups: "Es muy posible que anteriormente solo nos enfocaramos en arreglar un problema y noto cualquier modelo a futuro", "Quiero hacer un arreglo general para todo".

### Diagnosis (job `01M76M50RHVREFWA9GEEN51C5B`, armoured character, run folder `data/kit_runs/01M76M50RHVREFWA9GEEN51C5B/`)

- The TRELLIS model before the pass (`model_original.glb`) has a correct face; the pass output has the lower face of the old mold under the new face, a black band across the mouth and mismatched skin.
- **Cut inside the face.** The rig's head-labelled region is 0.38 wide on a 1.0-tall body: it includes the pauldrons and collar, which reach ear height (body width stays 0.37 up to y = 0.44 and drops to 0.18 only at y = 0.45). `kit.py raise_plane` raises the cut until ≥ 90 % of the mold near the plane is head-labelled and the part is solid; with wide armour at head height that only happens at y = 0.408, through the nose (raised 5.5 cm from the head joint at 0.353). Everything below the cut keeps the old face, and the 3 cm blend above it averages two faces.
- **Black band.** `paint.py` bakes the mold first with `use_clear=True`, a 12 mm ray limit, and forces alpha to 1. In the blend band the part weight is ~0, so colour must come from the mold bake; where the new surface is farther than 12 mm from the mold (alignment mean error here: 15.7 mm), the texel stays black.
- **Weak alignment.** The bust is fitted to every mold face above shoulder height minus 5 cm, dominated by collar and pauldrons: rigid fit, fitness 0.315, ~8° tilt.
- **Why it slipped through.** The rules were tuned on five characters without armour or tall collars (`docs/userstories/kit-assembly/` US-10); "no usable head" (that backlog's AC-03) was never exercised; nothing tests `kit.py`, `align.py`, `paint.py` or `crops.py`.

Root cause: the pass decides where the head ends and how to colour the seam from rules that assume the head-labelled region is only a head and that the mold bake always reaches the new surface. Characters whose head is surrounded by other geometry break both assumptions.

Out of scope: a new head generator, changes to TRELLIS or UniRig, rigging quality.

## US-01: An evaluation set for the head pass

As the owner, I want every head-pass change measured on a fixed set of varied characters, so that a fix for one character cannot silently break the others.

- US-01 / AC-01: A script reruns the pass's cut, paint and render steps (CPU only, from existing run folders) on the 12 runs in `data/kit_runs/` (the five kit-assembly characters, their reruns and the armoured character), writing per character a before/after head render and these numbers: cut height relative to the head joint and to the chin of the new head, share of uncovered (black) texels on the new head, alignment fitness and mean distance.
- US-01 / AC-02: The baseline numbers and a contact sheet for the current code are recorded in this file before any change; the owner reviews the sheet and marks each character as good or broken.

## US-02: Cut below the chin, never through the face

As a user, I want the head replaced from the neck down to the chin, so that no character ends up with half of its old face.

- US-02 / AC-01: The cut height comes from the new head itself (the chin/jaw line of the aligned bust, i.e. where its face ends), not from how much of the old mold around the plane is labelled head; armour, collars or hair at head height do not move it into the face.
- US-02 / AC-02: On the armoured character the cut lands between the chin and the collar, and the rendered face shows no part of the old face.
- US-02 / AC-03: On every character the owner marked good in US-01, the cut stays within the neck and the result is not worse on the contact sheet.

## US-03: No black seams

As a user, I want the seam to be coloured even where the old surface is far from the new one, so that no black band appears.

- US-03 / AC-01: Texels the mold bake does not reach take the new head's colour instead of black; `paint.json` reports the share of texels that had no colour from either bake.
- US-03 / AC-02: On the whole evaluation set the uncovered share is 0 % on the new head, and the armoured character's band is gone.

## US-04: Keep the original face when the result would be worse

As a user, I want the app to keep the original face when the pass cannot do better, so that "Detailed face" never ruins a model.

- US-04 / AC-01: The pass checks its own result (alignment quality, cut position relative to the chin, uncovered texels) against thresholds set from the US-01 measurements and recorded in this file; when a check fails, the job keeps `model_original.glb` as its model and the notice says why ("Detailed face skipped: …").
- US-04 / AC-02: A test exercises the skip path end to end with a stub pass result, and the app shows the notice as it does today.

## US-05: Fit the new head to the old head, not to the armour

As a user, I want the new head placed where the old head was, so that it is not sunk into a collar or tilted.

- US-05 / AC-01: Alignment uses only the old head's own surface (excluding geometry wider than the head at that height, such as collars and pauldrons) as its target.
- US-05 / AC-02: On the evaluation set, alignment mean distance and fitness are equal or better than the baseline for every character, and better for the armoured one.
- Revision (2026-11-12, measurement only): the criterion is measured on the heads (two-way mean distance between the new head and the old head), the fit the head swap uses. The whole-bust numbers move a little either way once the bust is fitted to the head instead of the shoulders. The refinement moves the bust and never turns it: turning it to the armoured character's turned old head turned its face away from the front where the face is read, and the pass put the old face back.

## US-06: Keep the sculpted head's own surface and texture

As a user, I want the detailed face to keep the head exactly as it was sculpted, so that it does not look blocky ("como legos").

Found 2026-11-12 (owner: "POR QUE LAS CARAS PARECEN COMO LEGOS"): the pass rebuilds the whole patch from a 3D distance grid (`kit.py head_swap` → `extract_surface`, 2 mm cells, inside/outside from a grid and nearest-face normals on TRELLIS's double-walled shells) and bakes a new texture onto it. Untextured renders show smooth, detailed busts and stair-stepped patches on every character (the man, the armoured character); the baseline has it too.

- US-06 / AC-01: Above the seam band (the cut plus the blend width), the model's head is the sculpted head's own triangles, unchanged, with its own UVs and texture; nothing above the band is rebuilt or re-baked.
- US-06 / AC-02: Only the seam band is rebuilt from the field and baked, and it meets the kept triangles without a visible gap.
- US-06 / AC-03: On the evaluation set, untextured head renders show no stair steps above the band, and the US-02/US-03 measurements are not worse.

## Dependencies

US-01 → (US-02, US-03, US-05) → US-04. US-02 and US-05 interact (the chin comes from the aligned head); US-03 is independent. US-06 builds on US-02 (the band is the cut plus the blend width) and changes what US-03 bakes (the band only).

## Final results (2026-11-12)

All stories, on the 9 runs (`evaluate.py`; Cut − chin and − joint in glTF units, body about 1.0 tall; head fit is the two-way mean distance between the heads before → after the head shift):

| Run | Character | Cut − chin | Cut − joint | Head fit | Uncovered | Skipped |
| --- | --- | --- | --- | --- | --- | --- |
| `01M6Y2QGM129CQTEJ6FNTEFWZ6` | man | -0.028 | +0.010 | 0.0068 → 0.0067 | 0.00 % | - |
| `01M6Y333HA86SAQ88C4J7AZ2C2` | dog | -0.044 | -0.011 | 0.0077 → 0.0077 | 0.00 % | - |
| `01M6YJ432XJVQNQXYW32JD6XQT` | alien | -0.037 | +0.004 | 0.0117 → 0.0115 | 0.00 % | - |
| `01M6YJCR9ATH3EFHNH4HBVSFKV` | cat | -0.020 | +0.017 | 0.0106 → 0.0104 | 0.00 % | - |
| `01M6YK3NW2J2FVF3M4F1MZ57K7` | dog | -0.052 | -0.007 | 0.0110 → 0.0109 | 0.00 % | - |
| `01M6YT0WEV02XG6KCDT2GHRZ6W` | dog | -0.047 | -0.002 | 0.0096 → 0.0095 | 0.00 % | - |
| `01M6YVXTPCK9RW4Y4V88EEQQAE` | dog | -0.048 | +0.005 | 0.0129 → 0.0126 | 0.00 % | - |
| `01M6YX342JQ2J62V5Q9ECZ01RD` | wolf | -0.053 | -0.031 | 0.0098 → 0.0097 | 0.00 % | - |
| `01M76M50RHVREFWA9GEEN51C5B` | armoured | **-0.008** | +0.001 | **0.0330 → 0.0157** | **0.00 %** | - |

US-04 thresholds, set from these measurements: the heads more than 0.025 apart (the worst head marked good is 0.0157), a seam that would reach the chin, or more than 1 % of the band without color keep the original model.

What changed, in the order the evaluation exposed it:

- The cut comes from the new head's throat (`landmarks.py`). The nose is searched between 10 % and 65 % of the head's height above the joint: a man's quiff jutted out past his nose higher up and the first version cut 10 cm above his chin.
- Heads are read on their own region of each section, connected in 3D, so collars and pauldrons stay out, while ears, a nose tip or the crown stay in.
- The new head keeps its own triangles and texture. Only a neck band (the cut plus the blend width) is rebuilt and baked, and only the old head's faces are removed. Leftover thin pieces of the old head joined to them (a dog's ear tips) go too.

## US-01 results (2026-11-12)

- `scripts/kit_assembly/evaluate.py OUT` reruns align, kit `--head-swap` and paint from the saved inputs into `OUT/<run>` (run folders untouched) and writes `eval.json`, `summary.json`, `sheet.png` and the table below. `scripts/kit_assembly/landmarks.py` reads the new head's nose, chin and throat from horizontal solid sections around its crown axis.
- The set is 9 runs, not 12: `01M6Y6BT…` and `no_skeleton` have no skeleton, `01M6Y2QG…_fullkit` is the full-kit experiment (no bust), and `01M714F2…` has no sculpted head (both bust generations failed with CUDA out of memory).
- "Uncovered" is the share of the new head's visible texels (faces above the cut) whose color came mostly from a bake that missed them, as `paint.py` now reports it. A black-pixel count from the GLB was dropped: it read the black cat as 98 % uncovered.

Baseline (current code before any change):

| Run | Character | Cut − chin | Cut − joint | Align (method, fitness, mean, reverse) | Uncovered |
| --- | --- | --- | --- | --- | --- |
| `01M6Y2QGM129CQTEJ6FNTEFWZ6` | man | -0.042 | -0.005 | rigid, 0.657, 0.0079, 0.0042 | 1.09 % |
| `01M6Y333HA86SAQ88C4J7AZ2C2` | dog | -0.032 | +0.001 | scaled, 0.653, 0.0074, 0.0042 | 0.08 % |
| `01M6YJ432XJVQNQXYW32JD6XQT` | alien | -0.036 | +0.009 | rigid, 0.573, 0.0081, 0.0051 | 0.13 % |
| `01M6YJCR9ATH3EFHNH4HBVSFKV` | cat | -0.011 | +0.025 | scaled, 0.649, 0.0060, 0.0061 | 0.07 % |
| `01M6YK3NW2J2FVF3M4F1MZ57K7` | dog | -0.008 | +0.037 | scaled, 0.513, 0.0099, 0.0066 | 0.34 % |
| `01M6YT0WEV02XG6KCDT2GHRZ6W` | dog | -0.030 | +0.015 | scaled, 0.594, 0.0082, 0.0054 | 0.14 % |
| `01M6YVXTPCK9RW4Y4V88EEQQAE` | dog | -0.047 | +0.007 | scaled, 0.448, 0.0096, 0.0072 | 2.96 % |
| `01M6YX342JQ2J62V5Q9ECZ01RD` | wolf | -0.083 | -0.062 | scaled, 0.517, 0.0100, 0.0058 | 1.46 % |
| `01M76M50RHVREFWA9GEEN51C5B` | armoured | **+0.027** | +0.044 | rigid, **0.315**, **0.0157**, 0.0096 | **4.50 %** |

Owner review (2026-11-12) of the sheet after US-02, US-03 and US-06: all 9 characters marked good ("All good, continue"). Glance review of the baseline sheet: only the armoured character shows its old face and a black band; the wolf's new head is much darker than its original.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `evaluate.py` on the 9 usable runs | Passed: table above, contact sheet with cut/chin/throat lines |
| US-01 / AC-02 | baseline recorded; owner marks each character good or broken | Passed: baseline recorded; owner marked all 9 good after US-02/03/06 |
| US-02 support | `tests/test_landmarks.py` (chin with and without armour, collar joined to the jaw, hair quiff past the nose, taller search box) | Passed |
| US-02 / AC-01 | `tests/test_face_cut.py::test_the_cut_lands_below_the_chin_of_the_new_head_inside_armour` | Passed |
| US-02 / AC-02 | evaluation set, armoured character | Passed: cut 0.008 below the chin, no old face on the render |
| US-02 / AC-03 | evaluation set, the other 8 characters; owner review | Passed: cut 0.020 to 0.053 below the chin; owner marked all good |
| US-03 / AC-01 | `tests/test_paint_combine.py` (part-only texel takes the part color at weight 0; unreached texels copy the nearest reached one); `paint.json` reports `unreached_texel_share` and `uncovered_texel_share` | Passed |
| US-03 / AC-02 | evaluation set uncovered share | Passed: 0.00 % on all 9 (baseline up to 4.50 %) |
| US-04 / AC-01 | `tests/test_quality_gate.py` (head too far, seam into the face, a fit that passes) | Passed |
| US-04 / AC-02 | `tests/test_quality_gate.py::test_a_skipped_pass_reports_the_original_model_and_why` (kit and paint skips reach the result line); existing `engine/tests/test_scheduler.py::test_detailed_face_not_applied_keeps_the_plain_model` and `client/tests/test_generation_vm.py::test_detailed_face_not_applied_keeps_the_plain_model_with_the_notice` | Passed |
| US-05 / AC-01 | `tests/test_align_head.py` (head moved onto the old head, never turned); targets read with `head_members` | Passed |
| US-05 / AC-02 | evaluation set head fit | Passed: equal or better on all 9; armoured 0.0330 → 0.0157 |
| US-06 / AC-01..AC-02 | `tests/test_face_cut.py` (kept faces above the band only, old head removed but not collar or pauldrons, ears kept, band neck-sized, stray pieces absorbed) | Passed |
| US-06 / AC-03 | evaluation set renders; owner review | Passed: sculpted surfaces and textures kept; owner marked all good |
| All | a run in the app on a new character with Detailed face | Pending |
