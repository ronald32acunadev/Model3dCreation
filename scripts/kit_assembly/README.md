# Kit assembly tools

Experiment tools for `docs/userstories/kit-assembly/`: generate a character's parts alone, cut them flat at the joints, cap them, fuse them into one surface, and give each part its own texture. Not used by the engine or the app.

Run every tool with the engine's interpreter (`engine/.venv/bin/python`, which ships `bpy`, `open3d`, `scipy`, `scikit-image`, `trimesh`) and under `ulimit -v 16000000`. Generation itself goes through the engine's HTTP API, so no tool loads TRELLIS.2.

## Head pass in one command

Generate the model in the app first, with high quality and **rig** on (the skeleton locates the head). Close the app, or leave its engine running if it uses TRELLIS.2, then:

```bash
python3 scripts/kit_assembly/run.py JOB_ID          # JOB_ID is a folder name in data/jobs
python3 scripts/kit_assembly/run.py JOB_ID --extra-seeds 11,22   # more tries for the head
```

It takes about 11 minutes with two seeds and writes `data/kit_runs/<JOB_ID>/model.glb` plus `compare_head.png` and `compare_front.png`. A rerun skips finished steps; delete the run folder to start over. A job without a skeleton ends with its own model. Only the head is sculpted: in the experiment, torsos came out as flat sheets and limbs bent differently from the body (see `docs/userstories/kit-assembly/`).

How the head swap works (`docs/userstories/detailed-face-general/`):

1. `align.py` fits the generated bust onto everything above the shoulders, then moves it (never turns it) onto the old head alone.
2. `landmarks.py` reads the new head's nose, chin and throat from horizontal solid sections around its crown, leaving out collars, armour or hair beside the head.
3. `kit.py --head-swap` cuts at the throat, below the chin. It keeps the bust's own triangles above a thin neck band, removes only the old head's faces, and rebuilds only the band from distance fields.
4. `paint.py` bakes the band's texture; the kept head keeps its own texture.
5. The pass keeps the original model, with the reason, when the heads sit more than 2.5 cm apart on average, when the seam would reach the face, or when more than 1 % of the band gets no color.

## Evaluation set

```bash
engine/.venv/bin/python scripts/kit_assembly/evaluate.py OUT_DIR        # every run in data/kit_runs with a sculpted head
cd scripts/kit_assembly && ../../engine/.venv/bin/python -m pytest tests  # unit tests
```

`evaluate.py` reruns align, kit and paint from each run folder's saved inputs into `OUT_DIR/<run>` (the run folders stay as they are), renders each head before and after, and prints a table: cut height against the chin and the head joint, alignment, head fit, and uncovered seam texels. Measure every head-pass change on it.

## Frame and units

All coordinates are in the glTF frame of the GLB files: +Y up, the subject faces +Z (toward the camera), the body is about 1.0 tall. Blender imports glTF as (x, -z, y); convert Blender world coordinates back with `(x, y, z)_gltf = (bx, bz, -by)`.

TRELLIS.2 meshes are hollow shells (outer and inner wall) and are split at UV seams. Weld positions before any topology or inside/outside test (`sdf.MeshField` handles the shell).

## Run folder

One folder per test image, `data/kit_runs/<job_id>/`:

| File | Written by | Content |
| --- | --- | --- |
| `input.png`, `mold.glb`, `mold_rig.glb`, `job.json` | `prepare` (copy) | The engine job's `trellis_input.png` (2048 px RGBA), `rig_input.glb`, `model.glb`, `job.json` |
| `rig.json`, `rig_labels.npz` | `rig_read.py` | Parts, joints, bone segments; per-vertex part labels |
| `crops/<part>.png`, `crops.json`, `crops.png` | `crops.py` | Part crops of the input image, projection fit, contact sheet |
| `parts/<part>.glb`, `parts.json` | engine jobs | Each crop generated alone (TRELLIS.2 `high`, same seed) |
| `aligned/<part>.glb`, `align.json` | `align.py` | Parts moved onto the mold |
| `pieces/<piece>.glb`, `fused.glb`, `fused_labels.npy`, `kit.json` | `kit.py` | Cut and capped pieces, fused surface, piece of each fused face |
| `patch.glb`, `remove_faces.npy`, `keep_head_faces.npy`, `kit.json` | `kit.py --head-swap` | Neck band; mold faces removed (mold.glb face order); bust faces kept (aligned bust face order) |
| `model.glb`, `paint.json` | `paint.py` | Fused mesh with one material and 4096 px texture per piece; for a head swap, the body, the kept head and the baked band |
| `compare_*.png`, `exploded.png` | `compare.py` | Review renders |

## Interfaces

`rig.json`:

```json
{
  "parts": ["torso", "head", "arm_px", "arm_nx", "leg_px", "leg_nx"],
  "joints": {
    "head": {"point": [x, y, z], "normal": [x, y, z], "segments": [[[ax, ay, az], [bx, by, bz]], ...]}
  },
  "missing": ["leg_nx"],
  "reason": null
}
```

- `parts[0]` is always `torso`. Other names: `head`, and `arm_px`/`arm_nx`/`leg_px`/`leg_nx` by the sign of the chain tip's x (`px` positive x, `nx` negative x).
- `point` is where the chain meets the torso (its first bone's head); `normal` is the unit direction of that bone, pointing into the part; `segments` are the chain's bones, head to tail.
- `reason` is a sentence when no head, arm, or leg was found (objects, models without a skeleton); then `parts` is `["torso"]` and later steps copy the mold.

`rig_labels.npz`: `positions` (N, 3) float64, the rigged mesh's rest-pose vertex positions; `labels` (N,) int8, index into `parts`.

`crops.json`: `{"flip": 1 or -1, "scale": s, "offset": [u0, v0], "iou": f, "head_ratio": f, "parts": {part: {"box": [x0, y0, x1, y1], "mask_box": [x0, y0, x1, y1]}}}`. Projection of a glTF point to input-image pixels: `u = scale * flip * x + offset[0]`, `v = -scale * y + offset[1]`.

`align.json`: `{part: {"matrix": 4x4 list, "scale": f, "mean_distance": f, "fitness": f, "head": {...} or null}}`, the matrix maps the generated part onto the mold. For a bust, `head` holds the head-only shift: `method` (`start` or `shifted`), the two-way mean distances between the heads before (`start_*`) and after it, or `error`.

`kit.json` (head swap): `face` (nose, chin, throat and cut heights, head axis, band radius), `blend` (band width), `removed_body_faces`, `kept_head_faces`, `planes`, or `skipped` with the reason. `paint.json` (head swap): the band's `part_texel_share`, `unreached_texel_share` and `uncovered_texel_share`, or `skipped`.
