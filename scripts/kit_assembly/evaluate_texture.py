"""Measure texture sharpness on saved runs: the model's front view next to the input image.

    python scripts/kit_assembly/evaluate_texture.py OUT [--runs data/kit_runs] [--only ID,...] [--after NAME]

Each run folder with an `input.png` and a `mold.glb` (the TRELLIS.2 model before any pass) is read:
its visible texels are splatted into a front view at the image's framing (texture.py), and the
sharpness of that view inside the silhouette is compared with the image's. With `--after NAME`,
`<run>/NAME` (a textured model with the same shape) is measured the same way next to it.
Writes OUT/<run>.png (input | before | after, with close-ups of the face, the chest and a hand),
OUT/summary.json and OUT/sheet.png, and prints a Markdown table. Read-only on the run folders.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from crops import ALPHA_THRESHOLD  # noqa: E402
from texture import FACING, frame, project, sharpness, splat, texels, visible  # noqa: E402

TILE = 360


def front_view(model: Path, alpha: np.ndarray, fit: tuple | None = None):
    """The model's front view at the image's framing, its mask, and the fit used."""
    scene = trimesh.load(model)
    meshes = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
    whole = trimesh.util.concatenate([trimesh.Trimesh(m.vertices, m.faces, process=False) for m in meshes])
    if fit is None:
        fit = frame(np.asarray(whole.vertices), np.asarray(whole.faces), alpha)
    scale, offset, flip, _ = fit
    pixels, colors, depth = [], [], []
    for mesh in meshes:
        texture = getattr(getattr(mesh.visual, "material", None), "baseColorTexture", None)
        if texture is None or getattr(mesh.visual, "uv", None) is None:
            continue
        found = texels(mesh, texture.size)
        seen = visible(whole, found.points, found.normals) & (found.normals[:, 2] > FACING[0])
        rgb = np.asarray(texture.convert("RGB"), np.float32) / 255
        pixels.append(project(found.points[seen], scale, offset, flip))
        colors.append(rgb[found.rows[seen], found.columns[seen]])
        depth.append(-found.points[seen, 2])
    if not pixels:
        raise ValueError(f"{model} has no base color texture")
    view, mask = splat(alpha.shape, np.vstack(pixels), np.vstack(colors), np.concatenate(depth))
    return view, mask, fit


def close_ups(run: Path, mask: np.ndarray) -> dict[str, tuple[int, int, int, int]]:
    """Square boxes around the face, the chest and a hand, from crops.json's part boxes when the
    run has them, otherwise from the silhouette's proportions."""
    ys, xs = np.nonzero(mask)
    top, bottom, middle = int(ys.min()), int(ys.max()), int(xs.mean())
    height = bottom - top
    square = lambda cx, cy, side: (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    boxes = {"face": square(middle, top + 0.09 * height, 0.18 * height),
             "chest": square(middle, top + 0.3 * height, 0.22 * height)}
    parts = json.loads((run / "crops.json").read_text())["parts"] if (run / "crops.json").exists() else {}
    arm = parts.get("arm_px") or parts.get("arm_nx")
    if "head" in parts:
        x0, y0, x1, y1 = parts["head"]["mask_box"]
        boxes["face"] = square((x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * 0.8)
    if arm is not None:
        x0, y0, x1, y1 = arm["mask_box"]
        boxes["hand"] = square((x0 + x1) / 2, y1 - 0.06 * height, 0.12 * height)
    else:
        boxes["hand"] = square(int(xs.min()) + 0.06 * height, top + 0.5 * height, 0.12 * height)
    return boxes


def to_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(array, 0, 1) * 255).astype(np.uint8))


def panel(run: Path, image: np.ndarray, views: list[np.ndarray], mask: np.ndarray, line: str) -> Image.Image:
    """A row per close-up: input, then each view, plus a header line."""
    boxes = close_ups(run, mask)
    columns = [image] + views
    out = Image.new("RGB", (len(columns) * (TILE + 6), len(boxes) * (TILE + 6) + 40), (30, 30, 30))
    draw = ImageDraw.Draw(out)
    draw.text((6, 6), run.name, fill=(255, 255, 255))
    draw.text((6, 22), line, fill=(255, 255, 255))
    for row, (name, box) in enumerate(boxes.items()):
        for column, array in enumerate(columns):
            tile = to_image(array).crop(box).resize((TILE, TILE), Image.LANCZOS)
            out.paste(tile, (column * (TILE + 6), 40 + row * (TILE + 6)))
        draw.text((6, 44 + row * (TILE + 6)), name, fill=(255, 255, 0))
    return out


def evaluate(run: Path, out: Path, after: str | None) -> dict:
    rgba = np.asarray(Image.open(run / "input.png").convert("RGBA"), np.float32) / 255
    image, alpha = np.ascontiguousarray(rgba[:, :, :3]), rgba[:, :, 3] * 255
    view, mask, fit = front_view(run / "mold.glb", alpha)
    common = mask & (alpha > ALPHA_THRESHOLD)
    reference = sharpness(image, common)
    result = {"silhouette_iou": fit[3], "image_sharpness": reference,
              "before": sharpness(view, common) / reference if reference else None}
    views = [view]
    if after is not None and (run / after).exists():
        view_after, _, _ = front_view(run / after, alpha, fit)
        result["after"] = sharpness(view_after, common) / reference if reference else None
        views.append(view_after)
    line = f"sharpness vs input: before {result['before']:.2f}" + (
        f"  after {result['after']:.2f}" if "after" in result else "")
    panel(run, image, views, common, line).save(out / f"{run.name}.png")
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--runs", type=Path, default=ROOT / "data" / "kit_runs")
    parser.add_argument("--only", help="comma-separated run folder names")
    parser.add_argument("--after", help="file name of the textured model inside each run folder")
    args = parser.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    runs = [r for r in sorted(args.runs.iterdir()) if (r / "input.png").exists() and (r / "mold.glb").exists()
            and (r / "rig.json").exists() and (only is None or r.name in only)
            and json.loads((r / "rig.json").read_text()).get("reason") is None and not r.name.endswith("_fullkit")]
    args.out.mkdir(parents=True, exist_ok=True)
    results = {}
    for run in runs:
        try:
            results[run.name] = evaluate(run, args.out, args.after)
        except (ValueError, OSError) as error:
            results[run.name] = {"error": str(error)}
        print(f"{run.name}: {results[run.name]}", flush=True)
    (args.out / "summary.json").write_text(json.dumps(results, indent=2))
    pictures = [Image.open(args.out / f"{name}.png") for name in results if (args.out / f"{name}.png").exists()]
    if pictures:
        width = max(p.width for p in pictures)
        sheet = Image.new("RGB", (width * 2 + 10, sum(p.height for p in pictures[::2]) + 10 * len(pictures)), (10, 10, 10))
        y = 0
        for index in range(0, len(pictures), 2):
            for k, picture in enumerate(pictures[index:index + 2]):
                sheet.paste(picture, (k * (width + 10), y))
            y += max(p.height for p in pictures[index:index + 2]) + 10
        sheet.save(args.out / "sheet.png")
    print("| Run | Silhouette IoU | Sharpness vs input, before | After |\n| --- | --- | --- | --- |")
    for name, result in results.items():
        if "error" in result:
            print(f"| `{name}` | {result['error']} | | |")
            continue
        after = f"{result['after']:.2f}" if "after" in result else "-"
        print(f"| `{name}` | {result['silhouette_iou']:.2f} | {result['before']:.2f} | {after} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
