"""Cut every piece flat at its joint, cap it, and fuse the pieces into one closed surface.

`python kit.py RUN_DIR [--h 0.001953125] [--faces 500000]` reads mold.glb, mold_labels.npy,
rig.json and aligned/<part>.glb, and writes pieces/<piece>.glb, fused.glb, fused_labels.npy
(the piece of each fused face) and kit.json. A part with no aligned model keeps the mold there.
A model with no parts (an object, or no skeleton) is copied unchanged to fused.glb and model.glb.

Fields are signed distances (negative inside): torso = mold minus every cutter, a piece = its part
within its cutter, and the fused surface is their union.
"""

import argparse
import json
import resource
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import open3d as o3d
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree

from sdf import CutterField, MeshField, edge_counts, extract_surface, signed_volume, weld

GRID_CELLS = 256  # occupancy cells along each model's longest side
RADIUS_PERCENTILE = 98  # capsule radius: this percentile of a bone's region distances
RADIUS_GROWTH = 1.1
SEAM_SAMPLES = 200  # plane samples per axis for the seam measure
# Generated parts are open where the body continues (a head's neck, a torso's sleeves): empty cells
# that see the mesh in 90 % of directions count as inside. 75 % filled regions out to the grid edge.
ENCLOSURE = 0.9
# A sculpted part with less than half the mold's volume in its region is a flat sheet, not a body
# part (seen on a torso crop, which TRELLIS.2 read as a loose shirt); the mold is used there.
MIN_VOLUME_RATIO = 0.5
VOLUME_STEP = 1 / 200
# Raising a cut: search up to this share of the part's region along the cut normal, in this many
# steps, for the first plane where the part is solid in SOLID_SHARE of its outline and its outline
# is at least MIN_OUTLINE of the mold's.
RAISE_LIMIT = 0.5
RAISE_STEPS = 40
SOLID_SHARE = 0.9
MIN_OUTLINE = 0.5
BLEND = 0.03  # glTF units over which the mold turns into the sculpted part above a cut
# Head swap: mold faces from SWAP_MARGIN below the cut upward are replaced; the patch reaches
# SWAP_OVERLAP further down, inside the kept body (glTF units, body about 1.0 tall).
SWAP_MARGIN = 0.01
SWAP_OVERLAP = 0.01
# Blended fields change faster than a distance (mold and part differ inside the band), so block
# skipping in extract_surface allows for twice the slope.
LIPSCHITZ = 2.0


class BoxBounded:
    """A field evaluated only inside its grid box; outside, the distance to the box (a lower bound)."""

    def __init__(self, field: MeshField):
        self.field = field
        self.lo, self.hi = field.bounds

    def __call__(self, points: np.ndarray) -> np.ndarray:
        gap = np.maximum(np.maximum(self.lo - points, points - self.hi), 0.0)
        values = np.linalg.norm(gap, axis=1)
        inside = np.flatnonzero(values == 0.0)
        if len(inside):
            values[inside] = self.field(points[inside])
        return values


def mesh_field(vertices: np.ndarray, faces: np.ndarray, enclosure: float | None = None) -> BoxBounded:
    vertices, faces = weld(vertices, faces)
    cell = float(np.ptp(vertices, axis=0).max()) / GRID_CELLS
    return BoxBounded(MeshField(vertices, faces, cell=cell, enclosure=enclosure))


def region_volume(field, lo: np.ndarray, hi: np.ndarray, step: float) -> float:
    """Volume where `field` is negative inside the box, counted on a grid."""
    axes = [np.arange(lo[a] + step / 2, hi[a], step) for a in range(3)]
    points = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    return float(sum((field(points[s:s + 1_000_000]) < 0).sum() for s in range(0, len(points), 1_000_000)) * step**3)


def cutter_box(cut: CutterField) -> tuple[np.ndarray, np.ndarray]:
    ends = np.array([p for a, b, _ in cut.segments for p in (a, b)])
    reach = max(r for _, _, r in cut.segments)
    return ends.min(axis=0) - reach, ends.max(axis=0) + reach


def load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path)
    return loaded.dump(concatenate=True) if isinstance(loaded, trimesh.Scene) else loaded


def cutter(joint: dict, region: np.ndarray, h: float) -> CutterField:
    """Plane at the joint, within capsules sized to the part's region of the mold."""
    segments = [(np.asarray(a), np.asarray(b)) for a, b in joint["segments"]]
    distances = np.stack([_segment_distance(region, a, b) for a, b in segments])
    nearest = np.argmin(distances, axis=0)
    capsules = []
    for index, (a, b) in enumerate(segments):
        own = distances[index, nearest == index]
        if len(own) == 0:
            continue
        radius = max(float(np.percentile(own, RADIUS_PERCENTILE)) * RADIUS_GROWTH + 2 * h, 0.01)
        capsules.append((a, b, radius))
    return CutterField(joint["point"], joint["normal"], capsules)


def _segment_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    axis = b - a
    t = np.clip((points - a) @ axis / max(float(axis @ axis), 1e-12), 0.0, 1.0)
    return np.linalg.norm(points - (a + t[:, None] * axis), axis=1)


def section(point: np.ndarray, cut: CutterField, field) -> tuple[np.ndarray, np.ndarray, float]:
    """Solid and outline (holes filled) of `field` on the plane through `point`, inside the cutter's capsules.

    Returns the two masks and the area of one sample cell.
    """
    normal = np.asarray(cut.normal)
    u = np.cross(normal, [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    reach = max(r for _, _, r in cut.segments) * 1.5
    s = np.linspace(-reach, reach, SEAM_SAMPLES)
    grid_u, grid_v = np.meshgrid(s, s, indexing="ij")
    points = point + grid_u.reshape(-1, 1) * u + grid_v.reshape(-1, 1) * v
    within = np.min([_segment_distance(points, a, b) - r for a, b, r in cut.segments], axis=0) < 0
    solid = (field(points) < 0).reshape(SEAM_SAMPLES, SEAM_SAMPLES)
    outline = ndimage.binary_fill_holes(solid).ravel() & within
    return solid.ravel() & within, outline, float((s[1] - s[0]) ** 2)


def seam(cut: CutterField, torso, part) -> dict:
    """Compare the torso's and the part's outlines on the cut plane, inside the cutter.

    Matching outlines (IoU near 1) meet face to face; a mismatch is a step the blend band has to bridge.
    """
    _, torso_outline, cell_area = section(cut.point, cut, torso)
    _, part_outline, _ = section(cut.point, cut, part)
    union = (torso_outline | part_outline).sum()
    return {
        "torso_area": float(torso_outline.sum() * cell_area),
        "part_area": float(part_outline.sum() * cell_area),
        "iou": float((torso_outline & part_outline).sum() / union) if union else 0.0,
    }


def raise_plane(cut: CutterField, region: np.ndarray, part, mold) -> float:
    """Distance to move the cut along its normal so it crosses the sculpted part where it is solid.

    A generated part is hollow and ragged where the body continued (a head's neck ends in a tube
    and a shirt collar); the mold, which is solid there, keeps everything below the raised plane.
    """
    reach = float(np.max((region - cut.point) @ cut.normal)) * RAISE_LIMIT
    for offset in np.linspace(0.0, reach, RAISE_STEPS):
        point = cut.point + offset * cut.normal
        solid, outline, _ = section(point, cut, part)
        _, mold_outline, _ = section(point, cut, mold)
        if outline.sum() and solid.sum() >= SOLID_SHARE * outline.sum() and outline.sum() >= MIN_OUTLINE * mold_outline.sum():
            return float(offset)
    return 0.0


class Blend:
    """The mold just above a cut, turning into the sculpted part over BLEND, so the seam has no step."""

    def __init__(self, cut: CutterField, mold, part):
        self.cut, self.mold, self.part = cut, mold, part

    def __call__(self, points: np.ndarray) -> np.ndarray:
        t = np.clip((points - self.cut.point) @ self.cut.normal / BLEND, 0.0, 1.0)
        weight = t * t * (3 - 2 * t)
        values = self.part(points)
        mixed = np.flatnonzero(weight < 1.0)
        if len(mixed):
            values[mixed] = weight[mixed] * values[mixed] + (1 - weight[mixed]) * self.mold(points[mixed])
        return values


def save(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    trimesh.Trimesh(vertices, faces, process=False).export(path)


def mesh_stats(vertices: np.ndarray, faces: np.ndarray) -> dict:
    boundary, nonmanifold = edge_counts(faces)
    return {"faces": int(len(faces)), "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "volume": signed_volume(vertices, faces) if len(faces) else 0.0}


def decimate(vertices: np.ndarray, faces: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    if len(faces) <= target:
        return vertices, faces
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(vertices), o3d.utility.Vector3iVector(faces.astype(np.int32)))
    mesh = mesh.simplify_quadric_decimation(target)
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    return np.asarray(mesh.vertices), np.asarray(mesh.triangles).astype(np.int64)


def cutter_json(cut: CutterField) -> dict:
    return {"point": cut.point.tolist(), "normal": cut.normal.tolist(),
            "segments": [[a.tolist(), b.tolist(), r] for a, b, r in cut.segments]}


def head_swap(run: Path, h: float, faces_target: int, report: dict, mold_mesh: trimesh.Trimesh,
              sources: dict, cutters: dict, started: float) -> int:
    """Write a head patch and the mold faces it replaces; the rest of the mold stays as it is.

    Remeshing and re-baking the whole body blurred the alien's and the cat's shirts. The patch is
    the sculpted head plus a neck band that turns from the mold into it; mold faces whose centers
    lie SWAP_MARGIN below the cut are removed, and the patch reaches SWAP_OVERLAP further down,
    inside the kept body, so no gap opens between them.
    """
    report["mode"] = "head_swap"
    if not isinstance(sources.get("head"), Blend):
        shutil.copy(run / "mold.glb", run / "model.glb")
        report["skipped"] = "no sculpted head passed the volume check"
        (run / "kit.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({"skipped": report["skipped"]}))
        return 0
    cut = cutters["head"]
    remove = CutterField(cut.point - SWAP_MARGIN * cut.normal, cut.normal, cut.segments)
    reach = CutterField(cut.point - (SWAP_MARGIN + SWAP_OVERLAP) * cut.normal, cut.normal, cut.segments)
    head = sources["head"]
    vertices, faces = extract_surface(lambda p: np.maximum(head(p), reach(p)), *cutter_box(reach), h, lipschitz=LIPSCHITZ)
    report["patch_raw"] = mesh_stats(vertices, faces)
    vertices, faces = decimate(vertices, faces, faces_target)
    report["patch"] = mesh_stats(vertices, faces)
    save(run / "patch.glb", vertices, faces)
    removed = remove(np.asarray(mold_mesh.triangles_center)) < 0
    report["removed_body_faces"] = int(removed.sum())
    report["kept_body_faces"] = int((~removed).sum())
    report["remove_cutter"] = cutter_json(remove)
    report["blend"] = BLEND
    report["planes"] = {"head": {"point": cut.point.tolist(), "normal": cut.normal.tolist()}}
    report["seconds"] = round(time.monotonic() - started, 1)
    report["peak_rss_gib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 2)
    (run / "kit.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"patch": report["patch"], "removed_body_faces": report["removed_body_faces"],
                      "kept_body_faces": report["kept_body_faces"], "seconds": report["seconds"]}))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--h", type=float, default=1 / 512)
    parser.add_argument("--faces", type=int, default=500_000)
    parser.add_argument("--head-swap", action="store_true",
                        help="keep the mold's body as it is and write only a head patch (patch.glb)")
    args = parser.parse_args(argv)
    run, h = Path(args.run), args.h
    rig = json.loads((run / "rig.json").read_text())
    started = time.monotonic()

    if len(rig["parts"]) == 1:
        shutil.copy(run / "mold.glb", run / "fused.glb")
        shutil.copy(run / "mold.glb", run / "model.glb")
        (run / "kit.json").write_text(json.dumps({"skipped": rig["reason"]}, indent=2))
        print(json.dumps({"skipped": rig["reason"]}))
        return 0

    mold_mesh = load_mesh(run / "mold.glb")
    labels = np.load(run / "mold_labels.npy")
    mold = mesh_field(np.asarray(mold_mesh.vertices), np.asarray(mold_mesh.faces))
    names = rig["parts"][1:]
    cutters = {
        part: cutter(rig["joints"][part], np.asarray(mold_mesh.vertices)[labels == index], h)
        for index, part in enumerate(names, start=1)
    }
    report: dict = {"h": h, "pieces": {}, "seams": {}, "cutters": {}, "volume_check": {}}
    sources: dict[str, BoxBounded] = {}
    every_cut = lambda p: np.min([cutters[q](p) for q in names], axis=0)
    for part in (["head"] if args.head_swap and "head" in names else ["torso"] + names):
        paths = [p for pattern in (f"{part}.glb", f"{part}@*.glb", f"{part}_bust.glb", f"{part}_bust@*.glb")
                 for p in sorted((run / "aligned").glob(pattern))]
        if part == "torso":
            box = (mold.lo, mold.hi)
            within = lambda field: (lambda p: np.maximum(field(p), -every_cut(p)))
        else:
            box = cutter_box(cutters[part])
            within = lambda field, q=part: (lambda p: np.maximum(field(p), cutters[q](p)))
        molded = region_volume(within(mold), *box, VOLUME_STEP)
        best = (None, mold, 0.0)
        ratios = {}
        for path in paths:
            part_mesh = load_mesh(path)
            candidate = mesh_field(np.asarray(part_mesh.vertices), np.asarray(part_mesh.faces), enclosure=ENCLOSURE)
            ratio = region_volume(within(candidate), *box, VOLUME_STEP) / molded if molded else 0.0
            ratios[path.name] = ratio
            if ratio > best[2]:
                best = (path.name, candidate, ratio)
        keep = best[2] >= MIN_VOLUME_RATIO
        sources[part] = best[1] if keep else mold
        report["volume_check"][part] = {"used": "sculpted" if keep else "mold", "file": best[0] if keep else None,
                                        "ratios": ratios, "mold_volume": molded}
        print(json.dumps({part: report["volume_check"][part]}), flush=True)

    sculpted = {part: sources[part] for part in names if sources.get(part, mold) is not mold}
    report["raised"] = {}
    for part, field in sculpted.items():
        region = np.asarray(mold_mesh.vertices)[labels == names.index(part) + 1]
        offset = raise_plane(cutters[part], region, field, mold)
        cut = cutters[part]
        cutters[part] = CutterField(cut.point + offset * cut.normal, cut.normal, cut.segments)
        blend = Blend(cutters[part], mold, field)
        box_lo, box_hi = cutter_box(cutters[part])
        blend.lo, blend.hi = np.minimum(field.lo, box_lo), np.maximum(field.hi, box_hi)
        sources[part] = blend
        report["raised"][part] = offset
        print(json.dumps({part: {"raised_by": round(offset, 4)}}), flush=True)

    if args.head_swap:
        return head_swap(run, h, args.faces, report, mold_mesh, sources, cutters, started)

    def piece_values(points: np.ndarray) -> np.ndarray:
        """Field value of every piece at the points: torso first, then the parts in `names` order."""
        cuts = np.stack([cutters[p](points) for p in names])
        torso = np.maximum(sources["torso"](points), -cuts.min(axis=0))
        return np.vstack([torso, np.maximum(np.stack([sources[p](points) for p in names]), cuts)])

    def fused(points: np.ndarray) -> np.ndarray:
        return piece_values(points).min(axis=0)

    lo = np.min([mold.lo] + [s.lo for s in sources.values()], axis=0)
    hi = np.max([mold.hi] + [s.hi for s in sources.values()], axis=0)
    (run / "pieces").mkdir(exist_ok=True)
    piece_names = ["torso"] + names
    for name in piece_names:
        if name == "torso":
            field = lambda p: np.maximum(sources["torso"](p), -np.min([cutters[q](p) for q in names], axis=0))
            box = (sources["torso"].lo, sources["torso"].hi)
        else:
            field = lambda p, q=name: np.maximum(sources[q](p), cutters[q](p))
            box = (sources[name].lo, sources[name].hi)
        vertices, faces = extract_surface(field, *box, h, lipschitz=LIPSCHITZ)
        save(run / "pieces" / f"{name}.glb", vertices, faces)
        report["pieces"][name] = mesh_stats(vertices, faces)
        print(json.dumps({name: report["pieces"][name]}), flush=True)
    for part in names:
        report["cutters"][part] = [{"a": a.tolist(), "b": b.tolist(), "radius": r} for a, b, r in cutters[part].segments]
        if part in sculpted:
            report["seams"][part] = seam(cutters[part], mold, sculpted[part])

    vertices, faces = extract_surface(fused, lo, hi, h, lipschitz=LIPSCHITZ)
    report["fused_raw"] = mesh_stats(vertices, faces)
    vertices, faces = decimate(vertices, faces, args.faces)
    report["fused"] = mesh_stats(vertices, faces)
    centroids = vertices[faces].mean(axis=1)
    # Faces take the mold's geodesic part labels; a sculpted part claims the faces in its cutter.
    _, nearest = cKDTree(np.asarray(mold_mesh.vertices)).query(centroids)
    face_labels = labels[nearest].astype(np.int8)
    for part in sculpted:
        face_labels[cutters[part](centroids) < 0] = names.index(part) + 1
    np.save(run / "fused_labels.npy", face_labels)
    report["blend"] = BLEND
    report["planes"] = {p: {"point": cutters[p].point.tolist(), "normal": cutters[p].normal.tolist()} for p in sculpted}
    save(run / "fused.glb", vertices, faces)
    report["seconds"] = round(time.monotonic() - started, 1)
    report["peak_rss_gib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 2)
    (run / "kit.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"fused_raw": report["fused_raw"], "fused": report["fused"], "seams": report["seams"],
                      "seconds": report["seconds"], "peak_rss_gib": report["peak_rss_gib"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
