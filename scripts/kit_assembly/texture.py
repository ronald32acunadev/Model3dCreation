"""Sharpen a model's texture with the full-resolution input image, without changing its shape.

`python texture.py MODEL.glb IMAGE.png OUT.glb [--report texture.json] [--no-paint]`

TRELLIS.2 reads the input image shrunk to 1024 px; the app keeps it at 2048 px. Every texel of the
model's base color textures is placed in 3D (its triangle's barycentric point) and projected on
the image with a front orthographic fit of the model's silhouette (crops.fit_projection). Texels
facing the camera that nothing hides (a ray toward the camera hits nothing) see the image.

The image does not land on the model exactly: the silhouette fit leaves details a few pixels off.
The visible texels are splatted into a front view of the current texture, the image is registered
on that view (an affine fit of the silhouettes, then dense optical flow), and only then sampled.
Texels take the image's fine detail over the model's own low frequencies (blur of the front view
minus blur of the registered image), so the image's lighting does not show as a seam; the image
weighs in as the surface faces the camera. Meshes, UVs, skeletons and every other byte of the GLB
stay as they are (glb_image.py).
"""

import argparse
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
import trimesh
from PIL import Image
from scipy import ndimage

from crops import ALPHA_THRESHOLD, alpha_box, fit_projection, project
from glb_image import base_color_images, image_bytes, read_glb, replace_image, write_glb

FACING = (0.25, 0.6)  # the image's weight rises from 0 to 1 as the normal's z goes from 0.25 to 0.6
EDGE_PX = 12  # the weight fades in over this many image pixels inside the silhouette
DETAIL_SIGMA = 16.0  # image pixels: finer than this comes from the image, coarser from the model
SHAPE_RANGE = 40.0  # registration pixels of outline distance the shape flow sees
FLOW_SMOOTH = 6.0  # registration pixels (at FLOW_PX): the flow is blurred this much, so it bends, never tears
AGREE_WINDOW = 41  # registration pixels: window of the local agreement between view and image
AGREE_BAND = (2.0, 10.0)  # registration pixels: the agreement compares coarse structure (eyes, sleeves,
# a logo's disc), not fine texture, which TRELLIS.2 may have painted wrong
AGREE = (0.2, 0.5)  # the image's weight rises from 0 to 1 as that agreement goes from 0.2 to 0.5
HIDDEN_OFFSET = 0.002  # glTF units: rays toward the camera start this far off the surface
FLOW_PX = 1024  # dense registration runs at this image size


@dataclass
class Texels:
    """The texels of one texture that lie on the model: their pixel, 3D point and normal."""

    rows: np.ndarray
    columns: np.ndarray
    points: np.ndarray
    normals: np.ndarray


def texels(mesh: trimesh.Trimesh, size: tuple[int, int]) -> Texels:
    """Rasterize the mesh's UV triangles into a texture of `size` (width, height)."""
    width, height = size
    uv = np.asarray(mesh.visual.uv, np.float64)
    corners = uv[mesh.faces]
    pixels = np.stack([corners[:, :, 0] * width, (1 - corners[:, :, 1]) * height], axis=-1)
    ids = np.full((height, width), -1, np.int32)
    order = np.argsort(-np.abs(np.cross(pixels[:, 1] - pixels[:, 0], pixels[:, 2] - pixels[:, 0])))
    for face in order:  # large triangles first, so slivers on shared edges are not overwritten away
        cv2.fillConvexPoly(ids, np.round(pixels[face] * 16).astype(np.int32), int(face), lineType=cv2.LINE_8, shift=4)
    rows, columns = np.nonzero(ids >= 0)
    faces = ids[rows, columns]
    a, b, c = (pixels[faces, k] for k in range(3))
    p = np.column_stack([columns + 0.5, rows + 0.5])
    v0, v1, v2 = b - a, c - a, p - a
    denominator = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    denominator[np.abs(denominator) < 1e-12] = 1e-12
    w1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / denominator
    w2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / denominator
    weights = np.clip(np.column_stack([1 - w1 - w2, w1, w2]), 0.0, 1.0)
    weights /= weights.sum(axis=1, keepdims=True)
    triangles = np.asarray(mesh.vertices)[mesh.faces[faces]]
    points = np.einsum("ij,ijk->ik", weights, triangles)
    normals = np.asarray(mesh.face_normals)[faces]
    return Texels(rows, columns, points, normals)


def visible(mesh: trimesh.Trimesh, points: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Points a ray toward the camera (+z) leaves without hitting the mesh."""
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.asarray(mesh.vertices, np.float32)),
                        o3d.core.Tensor(np.asarray(mesh.faces, np.uint32)))
    origins = points + HIDDEN_OFFSET * normals + np.array([0.0, 0.0, HIDDEN_OFFSET])
    rays = np.hstack([origins, np.tile([0.0, 0.0, 1.0], (len(points), 1))]).astype(np.float32)
    hits = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
    return ~np.isfinite(hits)


def frame(vertices: np.ndarray, faces: np.ndarray, alpha: np.ndarray) -> tuple[float, np.ndarray, int, float]:
    """The front projection that best overlaps the model's silhouette (its triangles drawn) with
    the image's alpha: scale, offset, x flip, and its silhouette IoU."""
    box = alpha_box(alpha)
    silhouette = alpha > ALPHA_THRESHOLD
    best = None
    for flip in (1, -1):
        scale, offset = fit_projection(vertices, box, flip)
        pixels = project(vertices, scale, offset, flip)
        covered = np.zeros(alpha.shape, np.uint8)
        cv2.fillPoly(covered, list(np.round(pixels[faces] * 4).astype(np.int32)), 1, lineType=cv2.LINE_8, shift=2)
        covered = covered.astype(bool)
        iou = float((covered & silhouette).sum() / max((covered | silhouette).sum(), 1))
        if best is None or iou > best[3]:
            best = (scale, offset, flip, iou)
    return best


def splat(shape: tuple[int, int], pixels: np.ndarray, colors: np.ndarray, depth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A front view: each image pixel takes its nearest texel's color, holes filled by closing."""
    height, width = shape
    x, y = np.round(pixels[:, 0]).astype(int), np.round(pixels[:, 1]).astype(int)
    inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    order = np.argsort(depth[inside])  # far first, so the nearest texel is written last
    image = np.zeros((height, width, 3), np.float32)
    covered = np.zeros((height, width), bool)
    image[y[inside][order], x[inside][order]] = colors[inside][order]
    covered[y[inside][order], x[inside][order]] = True
    filled = ndimage.binary_closing(covered, iterations=2)
    if (filled & ~covered).any():
        _, (rows, columns) = ndimage.distance_transform_edt(~covered, return_indices=True)
        holes = filled & ~covered
        image[holes] = image[rows[holes], columns[holes]]
    return image, filled


def register(view: np.ndarray, view_mask: np.ndarray, image: np.ndarray, image_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    """Where to read the image for each view pixel (a float map of image coordinates, x and y).

    An affine fit of the silhouettes first (ECC on the masks), then dense optical flow (DIS) on
    the silhouettes' signed distance to their outline, at FLOW_PX: the shapes match even where
    TRELLIS.2 painted the texture wrong, and a plain dark shirt gave flow on the texture nothing to
    hold on to (it moved the man's chest 30 px).
    """
    height, width = view_mask.shape
    warp = np.eye(2, 3, dtype=np.float32)
    small = FLOW_PX / max(height, width)
    size = (round(width * small), round(height * small))
    a = cv2.resize(view_mask.astype(np.float32), size)
    b = cv2.resize(image_mask.astype(np.float32), size)
    report = {"affine": None}
    try:
        _, warp_small = cv2.findTransformECC(a, b, np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE,
                                             (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
        warp = warp_small.copy()
        warp[:, 2] /= small
        report["affine"] = warp.tolist()
    except cv2.error as error:
        report["affine_error"] = str(error).splitlines()[-1]
    gx, gy = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    map_x = warp[0, 0] * gx + warp[0, 1] * gy + warp[0, 2]
    map_y = warp[1, 0] * gx + warp[1, 1] * gy + warp[1, 2]
    warped = cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR)
    warped_mask = cv2.remap(image_mask.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR) > 0.5
    shape_view = outline_distance(cv2.resize(view_mask.astype(np.uint8), size) > 0)
    shape_image = outline_distance(cv2.resize(warped_mask.astype(np.uint8), size) > 0)
    flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(shape_view, shape_image, None)
    flow = cv2.GaussianBlur(flow, (0, 0), FLOW_SMOOTH)
    flow = cv2.resize(flow, (width, height)) / small
    fx, fy = gx + flow[:, :, 0], gy + flow[:, :, 1]
    report["flow_mean_px"] = float(np.linalg.norm(flow[view_mask], axis=1).mean()) if view_mask.any() else 0.0
    final_x = warp[0, 0] * fx + warp[0, 1] * fy + warp[0, 2]
    final_y = warp[1, 0] * fx + warp[1, 1] * fy + warp[1, 2]
    return np.stack([final_x, final_y], axis=-1).astype(np.float32), report


def outline_distance(mask: np.ndarray) -> np.ndarray:
    """An 8-bit image of the signed distance to the mask's outline (bright inside), for flow."""
    inside = ndimage.distance_transform_edt(mask)
    outside = ndimage.distance_transform_edt(~mask)
    signed = np.clip(inside - outside, -SHAPE_RANGE, SHAPE_RANGE)
    return ((signed + SHAPE_RANGE) / (2 * SHAPE_RANGE) * 255).astype(np.uint8)


def agreement(view: np.ndarray, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Local normalized correlation of the view's and the image's coarse structure, per
    pixel (-1 to 1), read at FLOW_PX: low where the model's shape or pose differs from the
    picture (a head TRELLIS.2 turned, an arm in another pose)."""
    height, width = mask.shape
    small = FLOW_PX / max(height, width)
    size = (round(width * small), round(height * small))
    band = lambda a: cv2.GaussianBlur(a, (0, 0), AGREE_BAND[0]) - cv2.GaussianBlur(a, (0, 0), AGREE_BAND[1])
    a = band(cv2.cvtColor(cv2.resize(view, size), cv2.COLOR_RGB2GRAY))
    b = band(cv2.cvtColor(cv2.resize(image, size), cv2.COLOR_RGB2GRAY))
    box = lambda x: cv2.boxFilter(x, -1, (AGREE_WINDOW, AGREE_WINDOW))
    covariance = box(a * b) - box(a) * box(b)
    spread = np.sqrt(np.maximum(box(a * a) - box(a) ** 2, 0) * np.maximum(box(b * b) - box(b) ** 2, 0))
    score = np.where(spread > 1e-6, covariance / np.maximum(spread, 1e-6), 0.0).astype(np.float32)
    return cv2.resize(score, (width, height))


def smoothstep(value: np.ndarray, low: float, high: float) -> np.ndarray:
    t = np.clip((value - low) / (high - low), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def placement_px(view: np.ndarray, image: np.ndarray, mask: np.ndarray) -> float | None:
    """Median pixel distance between matched SIFT features of the view and the image (RANSAC inliers
    of a homography-free match: pairs within the silhouette, mutual best, ratio test)."""
    sift = cv2.SIFT_create(nfeatures=4000)
    m = mask.astype(np.uint8) * 255
    ka, da = sift.detectAndCompute((cv2.cvtColor(view, cv2.COLOR_RGB2GRAY) * 255).astype(np.uint8), m)
    kb, db = sift.detectAndCompute((cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) * 255).astype(np.uint8), m)
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return None
    pairs = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    distances = [np.hypot(*(np.subtract(ka[g.queryIdx].pt, kb[g.trainIdx].pt))) for g in good]
    distances = [d for d in distances if d < 60]  # a match farther than 60 px is a wrong pairing
    return float(np.median(distances)) if len(distances) >= 10 else None


def sharpness(view: np.ndarray, mask: np.ndarray) -> float:
    """Mean squared Laplacian of the gray view inside the eroded mask."""
    gray = cv2.cvtColor(view, cv2.COLOR_RGB2GRAY)
    inner = ndimage.binary_erosion(mask, iterations=4)
    return float((cv2.Laplacian(gray, cv2.CV_32F)[inner] ** 2).mean()) if inner.any() else 0.0


def sharpen(model: Path, image_path: Path, out: Path | None, paint: bool = True) -> dict:
    """Write the sharpened model to `out` (unless `paint` is off) and return the report."""
    rgba = np.asarray(Image.open(image_path).convert("RGBA"), np.float32) / 255
    image, alpha = np.ascontiguousarray(rgba[:, :, :3]), rgba[:, :, 3] * 255
    document, binary = read_glb(model)
    scene = trimesh.load(model)
    meshes = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
    whole = trimesh.util.concatenate([trimesh.Trimesh(m.vertices, m.faces, process=False) for m in meshes])
    scale, offset, flip, iou = frame(np.asarray(whole.vertices), np.asarray(whole.faces), alpha)
    report: dict = {"silhouette_iou": iou, "textures": {}}
    work = []
    by_image: dict[int, list] = {}
    images = base_color_images(document)
    decoded = {index: Image.open(io.BytesIO(image_bytes(document, binary, index))) for index in set(images.values())}
    for mesh in meshes:
        material = getattr(mesh.visual, "material", None)
        texture = getattr(material, "baseColorTexture", None)
        uv = getattr(mesh.visual, "uv", None)
        if texture is None or uv is None:
            continue
        index = next((i for i, im in decoded.items() if im.size == texture.size
                      and np.array_equal(np.asarray(im.convert("RGB"))[::97, ::97], np.asarray(texture.convert("RGB"))[::97, ::97])), None)
        if index is None:
            continue
        found = texels(mesh, texture.size)
        pixels = project(found.points, scale, offset, flip)
        seen = visible(whole, found.points, found.normals) & (found.normals[:, 2] > FACING[0])
        work.append((index, found, pixels, seen))
        by_image.setdefault(index, []).append(len(work) - 1)
    if not work:
        report["skipped"] = "the model has no base color texture"
        return report
    textures = {index: np.asarray(decoded[index].convert("RGBA"), np.float32) / 255 for index in by_image}
    colors = np.vstack([textures[i][f.rows[s], f.columns[s], :3] for i, f, _, s in work])
    pixels = np.vstack([p[s] for _, _, p, s in work])
    depth = -np.concatenate([f.points[s, 2] for _, f, _, s in work])
    view, view_mask = splat(alpha.shape, pixels, colors, depth)
    silhouette = alpha > ALPHA_THRESHOLD
    common = view_mask & silhouette
    report["before"] = {"sharpness": sharpness(view, common), "image_sharpness": sharpness(image, common),
                        "placement_px": placement_px(view, image, common)}
    coordinates, report["registration"] = register(view, view_mask, image, silhouette)
    registered = cv2.remap(image, coordinates[:, :, 0], coordinates[:, :, 1], cv2.INTER_LINEAR)
    registered_alpha = cv2.remap(alpha.astype(np.float32), coordinates[:, :, 0], coordinates[:, :, 1], cv2.INTER_LINEAR)
    before_agreement = agreement(view, image, common)
    after_agreement = agreement(view, registered, common)
    report["agreement"] = {"silhouette_fit": float(before_agreement[common].mean()) if common.any() else 0.0,
                           "registered": float(after_agreement[common].mean()) if common.any() else 0.0}
    detail = registered + cv2.GaussianBlur(view, (0, 0), DETAIL_SIGMA) - cv2.GaussianBlur(registered, (0, 0), DETAIL_SIGMA)
    inside = view_mask & (registered_alpha > ALPHA_THRESHOLD)
    edge = smoothstep(ndimage.distance_transform_edt(inside), 0.0, EDGE_PX).astype(np.float32)
    trust = cv2.GaussianBlur(smoothstep(after_agreement, *AGREE).astype(np.float32), (0, 0), 8.0)
    edge = edge * trust
    report["agreement"]["trusted_share"] = float((trust[common] > 0.5).mean()) if common.any() else 0.0
    if not paint:
        return report
    for index, members in by_image.items():
        texture = textures[index].copy()
        for k in members:
            _, found, pix, seen = work[k]
            x = np.clip(np.round(pix[seen, 0]).astype(int), 0, alpha.shape[1] - 1)
            y = np.clip(np.round(pix[seen, 1]).astype(int), 0, alpha.shape[0] - 1)
            where = [pix[seen, 1] - 0.5, pix[seen, 0] - 0.5]  # pixel centers sit at +0.5
            sample = np.stack([ndimage.map_coordinates(detail[:, :, c], where, order=1, mode="nearest")
                               for c in range(3)], axis=1)
            weight = (smoothstep(found.normals[seen, 2], *FACING) * edge[y, x])[:, None]
            rows, columns = found.rows[seen], found.columns[seen]
            texture[rows, columns, :3] = np.clip(weight * sample + (1 - weight) * texture[rows, columns, :3], 0, 1)
            report["textures"].setdefault(str(index), {"texels": 0, "painted": 0})
            report["textures"][str(index)]["texels"] += int(len(found.rows))
            report["textures"][str(index)]["painted"] += int((weight[:, 0] > 0.01).sum())
        textures[index] = texture
        buffer = io.BytesIO()
        Image.fromarray((texture * 255 + 0.5).astype(np.uint8), "RGBA").save(buffer, "PNG")
        document, binary = replace_image(document, binary, index, buffer.getvalue(), "image/png")
    colors_after = np.vstack([textures[i][f.rows[s], f.columns[s], :3] for i, f, _, s in work])
    view_after, _ = splat(alpha.shape, pixels, colors_after, depth)
    report["after"] = {"sharpness": sharpness(view_after, common), "placement_px": placement_px(view_after, image, common)}
    report["views"] = {"before": view, "after": view_after, "image": image, "mask": common}
    if out is not None:
        write_glb(out, document, binary)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("model", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-paint", action="store_true", help="measure only")
    args = parser.parse_args(argv)
    report = sharpen(args.model, args.image, args.out, paint=not args.no_paint)
    report.pop("views", None)
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
