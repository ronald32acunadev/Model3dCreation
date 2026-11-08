"""Cut each part out of the input image at full resolution, using the mold projected onto it.

`python crops.py RUN_DIR` reads input.png, mold.glb, rig.json and rig_labels.npz, and writes
crops/<part>.png, crops.json and crops.png (a contact sheet). See README.md for crops.json.
"""

import json
import sys
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.spatial import cKDTree

from parts import geodesic_labels

ALPHA_THRESHOLD = 0.8 * 255  # TRELLIS.2 crops its input to alpha above this
DILATION = 0.01  # part masks grow by this fraction of the image height
MARGIN = 0.02  # crop boxes grow by this fraction of the image height
TRELLIS_MAX_SIDE = 1024  # TRELLIS.2 shrinks its input so the longer side is at most this


def load_mold(run: Path, rig: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mold vertices, faces, and a part label per vertex; the labels are saved as mold_labels.npy."""
    mesh = trimesh.load(run / "mold.glb", force="mesh")
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    rigged = np.load(run / "rig_labels.npz")
    _, nearest = cKDTree(rigged["positions"]).query(vertices)
    labels = geodesic_labels(vertices, faces, rig, rigged["labels"][nearest])
    np.save(run / "mold_labels.npy", labels)
    return vertices, faces, labels


def alpha_box(alpha: np.ndarray) -> np.ndarray:
    """[x0, y0, x1, y1] of the pixels TRELLIS.2 keeps."""
    ys, xs = np.nonzero(alpha > ALPHA_THRESHOLD)
    return np.array([xs.min(), ys.min(), xs.max(), ys.max()], np.float64)


def fit_projection(vertices: np.ndarray, box: np.ndarray, flip: int) -> tuple[float, np.ndarray]:
    """Scale and offset that map the mold's front view (x, y) onto the image box."""
    x, y = flip * vertices[:, 0], vertices[:, 1]
    scale = (box[3] - box[1]) / (y.max() - y.min())
    offset = np.array([
        (box[0] + box[2]) / 2 - scale * (x.min() + x.max()) / 2,
        (box[1] + box[3]) / 2 + scale * (y.min() + y.max()) / 2,
    ])
    return scale, offset


def project(vertices: np.ndarray, scale: float, offset: np.ndarray, flip: int) -> np.ndarray:
    """Image pixel (u, v) of each vertex."""
    return np.column_stack([scale * flip * vertices[:, 0] + offset[0], -scale * vertices[:, 1] + offset[1]])


def label_map(pixels: np.ndarray, depth: np.ndarray, faces: np.ndarray, face_labels: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Front-most part label + 1 at each pixel (0 where the mold does not cover)."""
    image = Image.new("L", size, 0)
    draw = ImageDraw.Draw(image)
    order = np.argsort(depth[faces].mean(axis=1))  # back to front: +Z faces the camera
    for face in order:
        draw.polygon([tuple(p) for p in pixels[faces[face]]], fill=int(face_labels[face]) + 1)
    return np.asarray(image)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: crops.py RUN_DIR", file=sys.stderr)
        return 2
    run = Path(argv[1])
    rig = json.loads((run / "rig.json").read_text())
    image = Image.open(run / "input.png").convert("RGBA")
    rgba = np.asarray(image)
    alpha = rgba[:, :, 3]
    height = alpha.shape[0]
    vertices, faces, labels = load_mold(run, rig)
    corner = labels[faces]
    face_labels = np.where(corner[:, 1] == corner[:, 2], corner[:, 1], corner[:, 0])  # majority of three
    box = alpha_box(alpha)
    silhouette = alpha > ALPHA_THRESHOLD

    best = None
    for flip in (1, -1):
        scale, offset = fit_projection(vertices, box, flip)
        covered = label_map(project(vertices, scale, offset, flip), vertices[:, 2], faces, np.zeros(len(faces), int), image.size) > 0
        iou = (covered & silhouette).sum() / (covered | silhouette).sum()
        if best is None or iou > best[0]:
            best = (iou, flip, scale, offset)
    iou, flip, scale, offset = best
    parts_map = label_map(project(vertices, scale, offset, flip), vertices[:, 2], faces, face_labels, image.size)

    crops_dir = run / "crops"
    crops_dir.mkdir(exist_ok=True)
    body_scale = min(1.0, TRELLIS_MAX_SIDE / max(image.size))
    info = {"flip": flip, "scale": float(scale), "offset": offset.tolist(), "iou": float(iou), "parts": {}}
    dilation = max(1, round(DILATION * height))
    margin = round(MARGIN * height)
    for index, part in enumerate(rig["parts"]):
        own = parts_map == index + 1
        if not own.any():
            continue
        ys, xs = np.nonzero(own)
        mask_box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        mask = ndimage.binary_dilation(own, iterations=dilation) & (alpha > 0)
        ys, xs = np.nonzero(mask)
        crop_box = [
            max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin),
            min(image.width, int(xs.max()) + 1 + margin), min(image.height, int(ys.max()) + 1 + margin),
        ]
        x0, y0, x1, y1 = crop_box
        crop = rgba[y0:y1, x0:x1].copy()
        crop[:, :, 3] = np.where(mask[y0:y1, x0:x1], crop[:, :, 3], 0)
        Image.fromarray(crop).save(crops_dir / f"{part}.png")
        part_scale = min(1.0, TRELLIS_MAX_SIDE / max(x1 - x0, y1 - y0))
        info["parts"][part] = {"box": crop_box, "mask_box": mask_box, "pixel_gain": part_scale / body_scale}
    info["head_ratio"] = info["parts"].get("head", {}).get("pixel_gain")
    (run / "crops.json").write_text(json.dumps(info, indent=2))
    contact_sheet(crops_dir, info, parts_map, run / "crops.png")
    print(json.dumps({"iou": round(iou, 3), "flip": flip, "head_ratio": info["head_ratio"],
                      "sizes": {p: [v["box"][2] - v["box"][0], v["box"][3] - v["box"][1]] for p, v in info["parts"].items()}}))
    return 0


def contact_sheet(crops_dir: Path, info: dict, parts_map: np.ndarray, path: Path) -> None:
    """Part map and each crop on gray, 300 px tall, with its name."""
    palette = np.array([[60, 60, 60], [200, 200, 200], [230, 80, 80], [80, 160, 230], [80, 200, 120], [230, 190, 60], [180, 90, 200], [90, 200, 200]], np.uint8)
    tiles = [Image.fromarray(palette[np.minimum(parts_map, len(palette) - 1)])]
    names = ["parts"]
    for part in info["parts"]:
        tile = Image.new("RGBA", Image.open(crops_dir / f"{part}.png").size, (128, 128, 128, 255))
        tile.alpha_composite(Image.open(crops_dir / f"{part}.png"))
        tiles.append(tile)
        names.append(part)
    tiles = [t.convert("RGB").resize((max(1, round(t.width * 300 / t.height)), 300)) for t in tiles]
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 10 * len(tiles), 330), (40, 40, 40))
    x = 0
    for tile, name in zip(tiles, names):
        sheet.paste(tile, (x, 30))
        ImageDraw.Draw(sheet).text((x + 4, 8), name, fill=(255, 255, 255))
        x += tile.width + 10
    sheet.save(path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
