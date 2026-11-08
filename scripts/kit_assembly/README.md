# Kit assembly tools

Experiment tools for `docs/userstories/kit-assembly/`: generate a character's parts alone, cut them flat at the joints, cap them, fuse them into one surface, and give each part its own texture. Not used by the engine or the app.

Run every tool with the engine's interpreter (`engine/.venv/bin/python`, which ships `bpy`, `open3d`, `scipy`, `scikit-image`, `trimesh`) and under `ulimit -v 16000000`. Generation itself goes through the engine's HTTP API, so no tool loads TRELLIS.2.

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
| `model.glb`, `paint.json` | `paint.py` | Fused mesh with one material and 4096 px texture per piece |
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

`align.json`: `{part: {"matrix": 4x4 list, "scale": f, "mean_distance": f, "fitness": f}}`, the matrix maps the generated part onto the mold.
