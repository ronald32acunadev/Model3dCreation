"""Cut every piece flat at its joint, cap it, and fuse the pieces into one closed surface.

`python kit.py RUN_DIR [--h 0.001953125] [--faces 500000]` reads mold.glb, mold_labels.npy,
rig.json and aligned/<part>.glb, and writes pieces/<piece>.glb, fused.glb, fused_labels.npy
(the piece of each fused face) and kit.json. With --head-swap it writes the seam band (patch.glb),
remove_faces.npy and keep_head_faces.npy instead (see head_swap). A part with no aligned model keeps the mold there.
A model with no parts (an object, or no skeleton) is copied unchanged to fused.glb and model.glb.

Fields are signed distances (negative inside): torso = mold minus every cutter, a piece = its part
within its cutter, and the fused surface is their union.
"""

import argparse
import json
from dataclasses import dataclass
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

from landmarks import find_face, head_members, plane_height
from sdf import CutterField, MeshField, capsule_distance, edge_counts, extract_surface, signed_volume, weld

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
# The raised cut must also cross the mold where OWN_SHARE of its surface points within LABEL_SLAB
# of the plane (inside the cutter) are labeled as the part.
OWN_SHARE = 0.9
LABEL_SLAB = 0.005
BLEND = 0.03  # glTF units over which the mold turns into the sculpted part above a cut
# Head swap: mold faces from SWAP_MARGIN below the cut upward are replaced; the patch reaches
# SWAP_OVERLAP further down, inside the kept body (glTF units, body about 1.0 tall).
SWAP_MARGIN = 0.01
SWAP_OVERLAP = 0.01
# Head swap: the cut comes from the sculpted head's own face (landmarks.py), at its throat and
# always far enough below its chin for a blend of at least BLEND_MIN. The band's capsules reach
# CUT_MARGIN past both heads at the band's heights; BAND_OVERLAP is how far the rebuilt band runs
# into the kept sculpted head, so no gap opens between them.
BLEND_MIN = 0.008
CUT_MARGIN = 0.01
BAND_OVERLAP = 0.004
# The pass keeps the original model when its own result is worse. The heads' two-way mean distance
# after alignment ran 0.0067 to 0.0157 on the evaluation set the owner marked good
# (docs/userstories/detailed-face-general/README.md, US-04); MAX_HEAD_GAP leaves room above that.
MAX_HEAD_GAP = 0.025
STRAY_SHARE = 0.05  # a leftover piece of old head below this share of the removed faces goes too
SEAM_SLACK = 0.004  # sections are read this far below the lowest face they classify
CELL_TOP = 0.004  # sections are read this far above the highest point
NO_BUST_DROP = 0.1  # a head without a bust crop is searched this far below its joint
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


def raise_plane(cut: CutterField, region: np.ndarray, part, mold, mold_points: np.ndarray, own: np.ndarray) -> float:
    """Distance to move the cut along its normal so it crosses the sculpted part where it is solid,
    and the mold where its surface already belongs to the part.

    A generated part is hollow and ragged where the body continued (a head's neck ends in a tube
    and a shirt collar); the mold, which is solid there, keeps everything below the raised plane.
    `own` marks the mold points labeled as the part: a cut through the mold's shirt collar mixed
    the bust's collar with the body's and left it ragged (the man), so the cut moves up to skin.
    """
    reach = float(np.max((region - cut.point) @ cut.normal)) * RAISE_LIMIT
    inside = np.min([capsule_distance(mold_points, a, b, r) for a, b, r in cut.segments], axis=0) < 0
    heights = (mold_points - cut.point) @ cut.normal
    for offset in np.linspace(0.0, reach, RAISE_STEPS):
        point = cut.point + offset * cut.normal
        solid, outline, _ = section(point, cut, part)
        _, mold_outline, _ = section(point, cut, mold)
        if not (outline.sum() and solid.sum() >= SOLID_SHARE * outline.sum() and outline.sum() >= MIN_OUTLINE * mold_outline.sum()):
            continue
        slab = inside & (np.abs(heights - offset) < LABEL_SLAB)
        if slab.any() and own[slab].mean() >= OWN_SHARE:
            return float(offset)
    return 0.0


def absorb_strays(mesh: trimesh.Trimesh, removed: np.ndarray, candidates: np.ndarray, share: float) -> np.ndarray:
    """`removed` plus every connected piece of `candidates` faces that touches it and has fewer than
    `share` times its faces: thin ends of the old head (a dog's ear tips) that the sections, read in
    3 mm cells, miss. Armour beside the head is not joined to it and stays."""
    result = removed.copy()
    loose = candidates & ~removed
    if not loose.any() or not removed.any():
        return result
    welded = trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.faces), process=False)
    welded.merge_vertices(merge_tex=True, merge_norm=True)  # TRELLIS meshes are split at UV seams
    pairs = welded.face_adjacency
    inner = pairs[loose[pairs[:, 0]] & loose[pairs[:, 1]]]
    pieces = trimesh.graph.connected_components(inner, nodes=np.flatnonzero(loose), min_len=1)
    touching = set(pairs[loose[pairs[:, 0]] & removed[pairs[:, 1]], 0]) | set(pairs[loose[pairs[:, 1]] & removed[pairs[:, 0]], 1])
    limit = share * removed.sum()
    for piece in pieces:
        piece = np.asarray(piece, dtype=np.int64)  # trimesh returns lone faces as lists
        if len(piece) < limit and touching.intersection(piece.tolist()):
            result[piece] = True
    return result


def quality_problem(head_fit: dict | None, info: dict, width: float) -> str | None:
    """Why the head swap would make the model worse, or None: the new head sits too far from the
    old one, or the neck seam would reach the face."""
    if head_fit and "mean_distance" in head_fit:
        gap = head_fit["mean_distance"] + head_fit["reverse_mean_distance"]
        if gap > MAX_HEAD_GAP:
            return f"the new head does not match the old one ({100 * gap:.1f} cm apart on average)"
    if info["cut_y"] + width > info["chin_y"] + 1e-6:
        return "the neck seam would reach the face"
    return None


@dataclass
class HeadCut:
    """Where the head swap cuts, and which faces it keeps and removes (face_cut)."""

    cutter: CutterField  # the cut plane, with capsules as wide as the heads at the seam band
    width: float  # blend width: the band runs from the cut to `width` above it
    keep_part: np.ndarray  # per sculpted-head face: kept as sculpted (head, above the band)
    remove_mold: np.ndarray  # per mold face: removed (old head, above the cut)
    info: dict


def face_cut(cut: CutterField, part, mold, part_mesh: trimesh.Trimesh, mold_mesh: trimesh.Trimesh, joint_y: float,
             lowest: float, h: float, mold_labeled: np.ndarray | None = None) -> HeadCut:
    """The head swap's cut at the sculpted head's throat, from its own face, with the faces it keeps.

    Only the seam band (the cut to the blend width above it) is rebuilt from the fields; above it
    the sculpted head keeps its own triangles and texture (rebuilding the whole head through a 2 mm
    grid made faces look like stacked bricks). Faces count as head when they lie on the head's
    own region of each horizontal section, so a collar, pauldrons or hair beside the head stay out
    of both what is removed and what is kept. Raises ValueError when the sculpted head has no
    readable face or is not solid at the cut.

    Raising the cut until the mold around it was labeled head put it through the armoured
    character's nose: its pauldrons and collar reach ear height and carry the head label.
    """
    face = find_face(part, lowest, float(part.hi[1]), joint_y)
    cut_y = min(face.throat_y, face.chin_y - BLEND_MIN)
    width = float(np.clip(face.chin_y - cut_y, BLEND_MIN, BLEND))
    normal = np.asarray(cut.normal, float)
    here = plane_height({"point": cut.point, "normal": normal}, face.axis[0], face.chin_z)
    point = np.asarray(cut.point, float) + (cut_y - here) * normal[1] * normal

    def members(field, centers: np.ndarray, below: float) -> np.ndarray:
        """Faces (by center) at most `below` under the cut that lie on the head's own region."""
        chosen = (centers - point) @ normal > -below
        result = np.zeros(len(centers), bool)
        if chosen.any():
            top = float(centers[chosen, 1].max()) + CELL_TOP
            bottom = cut_y - below - SEAM_SLACK
            result[chosen] = head_members(field, face.axis, bottom, top, joint_y, centers[chosen])
        return result

    part_centers = np.asarray(part_mesh.triangles_center)
    mold_centers = np.asarray(mold_mesh.triangles_center)
    part_head = members(part, part_centers, SWAP_MARGIN + SWAP_OVERLAP)
    mold_head = members(mold, mold_centers, SWAP_MARGIN + SWAP_OVERLAP)
    heights = lambda centers: (centers - point) @ normal
    keep_part = part_head & (heights(part_centers) > width)
    remove_mold = mold_head & (heights(mold_centers) > -SWAP_MARGIN)
    if mold_labeled is not None:  # mold faces labeled head, per face
        remove_mold = absorb_strays(mold_mesh, remove_mold, mold_labeled & (heights(mold_centers) > 0), STRAY_SHARE)
    if not keep_part.any():
        raise ValueError("the sculpted head has no surface above its neck")
    # The band's capsules reach the heads at the band's heights only, so they stay neck-sized.
    band = lambda centers, head: centers[head & (heights(centers) < width + BAND_OVERLAP)]
    reached = np.vstack([band(part_centers, part_head), band(mold_centers, mold_head)])
    segments = [(np.asarray(a, float), np.asarray(b, float)) for a, b, _ in cut.segments]
    reach = np.min([_segment_distance(reached, a, b) for a, b in segments], axis=0) if len(reached) else np.zeros(1)
    radius = float(reach.max()) + CUT_MARGIN + 2 * h
    moved = CutterField(point, normal, [(a, b, radius) for a, b in segments])
    solid, outline, _ = section(point, moved, part)
    if not outline.sum() or solid.sum() < SOLID_SHARE * outline.sum():
        raise ValueError("the sculpted head is not solid at its neck")
    info = {"nose_y": face.nose_y, "chin_y": face.chin_y, "throat_y": face.throat_y, "chin_z": face.chin_z,
            "axis": list(face.axis), "alone_from_y": face.alone_from_y, "cut_y": cut_y, "radius": radius}
    return HeadCut(moved, width, keep_part, remove_mold, info)


class Blend:
    """The mold just above a cut, turning into the sculpted part over `width`, so the seam has no step."""

    def __init__(self, cut: CutterField, mold, part, width: float = BLEND):
        self.cut, self.mold, self.part, self.width = cut, mold, part, width

    def __call__(self, points: np.ndarray) -> np.ndarray:
        t = np.clip((points - self.cut.point) @ self.cut.normal / self.width, 0.0, 1.0)
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


def head_swap(run: Path, h: float, faces_target: int, report: dict, mold_mesh: trimesh.Trimesh,
              sources: dict, cutters: dict, started: float, head_cut: HeadCut | None) -> int:
    """Write the seam band (patch.glb) and which faces of the mold and of the sculpted head stay.

    Remeshing and re-baking the whole body blurred the alien's and the cat's shirts, and rebuilding
    the whole head made it blocky. Only the band, which turns from the mold into the sculpted head
    over the blend width, is rebuilt: it reaches SWAP_OVERLAP into the kept body below and
    BAND_OVERLAP into the kept head above, so no gap opens. remove_faces.npy flags the mold faces
    the old head made (mold.glb face order) and keep_head_faces.npy the sculpted head faces kept
    (face order of the aligned head as kit.load_mesh reads it).
    """
    report["mode"] = "head_swap"
    if head_cut is None:
        shutil.copy(run / "mold.glb", run / "model.glb")
        report["skipped"] = report.pop("skip_reason", None) or "no sculpted head passed the volume check"
        (run / "kit.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({"skipped": report["skipped"]}))
        return 0
    cut = cutters["head"]
    reach = CutterField(cut.point - (SWAP_MARGIN + SWAP_OVERLAP) * cut.normal, cut.normal, cut.segments)
    band_top = cut.point + (head_cut.width + BAND_OVERLAP) * cut.normal
    head = sources["head"]
    within = lambda p: np.maximum(np.maximum(head(p), reach(p)), (p - band_top) @ cut.normal)
    lo, hi = cutter_box(reach)
    vertices, faces = extract_surface(within, lo, hi, h, lipschitz=LIPSCHITZ)
    report["patch_raw"] = mesh_stats(vertices, faces)
    vertices, faces = decimate(vertices, faces, faces_target)
    report["patch"] = mesh_stats(vertices, faces)
    save(run / "patch.glb", vertices, faces)
    np.save(run / "remove_faces.npy", head_cut.remove_mold)
    np.save(run / "keep_head_faces.npy", head_cut.keep_part)
    report["removed_body_faces"] = int(head_cut.remove_mold.sum())
    report["kept_body_faces"] = int((~head_cut.remove_mold).sum())
    report["kept_head_faces"] = int(head_cut.keep_part.sum())
    report["blend"] = head_cut.width
    report["band_overlap"] = BAND_OVERLAP
    report["planes"] = {"head": {"point": cut.point.tolist(), "normal": cut.normal.tolist()}}
    report["seconds"] = round(time.monotonic() - started, 1)
    report["peak_rss_gib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 2)
    (run / "kit.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"patch": report["patch"], "removed_body_faces": report["removed_body_faces"],
                      "kept_head_faces": report["kept_head_faces"], "seconds": report["seconds"]}))
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
    # Molds can be open too: the alien's read as hollow in the neck (its head held a tenth of the
    # dog's solid volume) and the blend into the head left holes and strands there.
    mold = mesh_field(np.asarray(mold_mesh.vertices), np.asarray(mold_mesh.faces), enclosure=ENCLOSURE)
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
    if args.head_swap and "head" in sculpted:
        joint_y = float(rig["joints"]["head"]["point"][1])
        crops = json.loads((run / "crops.json").read_text()) if (run / "crops.json").exists() else {"parts": {}}
        lowest = crops["parts"].get("head_bust", {}).get("y_cut", joint_y - NO_BUST_DROP)
        try:
            bust = load_mesh(run / "aligned" / report["volume_check"]["head"]["file"])
            corner = labels[np.asarray(mold_mesh.faces)]
            face_labels = np.where(corner[:, 1] == corner[:, 2], corner[:, 1], corner[:, 0])
            head_cut = face_cut(cutters["head"], sculpted["head"], mold, bust, mold_mesh, joint_y, lowest, h,
                                face_labels == names.index("head") + 1)
            cutters["head"], width, report["face"] = head_cut.cutter, head_cut.width, head_cut.info
            align = json.loads((run / "align.json").read_text()) if (run / "align.json").exists() else {}
            head_fit = align.get(Path(report["volume_check"]["head"]["file"]).stem, {}).get("head")
            problem = quality_problem(head_fit, head_cut.info, width)
            if problem is not None:
                raise ValueError(problem)
        except ValueError as error:
            report["skip_reason"] = f"{error}"
            sources["head"] = mold
        else:
            field = sculpted["head"]
            blend = Blend(cutters["head"], mold, field, width)
            box_lo, box_hi = cutter_box(cutters["head"])
            blend.lo, blend.hi = np.minimum(field.lo, box_lo), np.maximum(field.hi, box_hi)
            sources["head"] = blend
            report["blend"] = width
            print(json.dumps({"head": {"face": report["face"], "blend": width}}), flush=True)
        sculpted = {}
    for part, field in sculpted.items():
        region = np.asarray(mold_mesh.vertices)[labels == names.index(part) + 1]
        offset = raise_plane(cutters[part], region, field, mold, np.asarray(mold_mesh.vertices),
                             labels == names.index(part) + 1)
        cut = cutters[part]
        cutters[part] = CutterField(cut.point + offset * cut.normal, cut.normal, cut.segments)
        blend = Blend(cutters[part], mold, field)
        box_lo, box_hi = cutter_box(cutters[part])
        blend.lo, blend.hi = np.minimum(field.lo, box_lo), np.maximum(field.hi, box_hi)
        sources[part] = blend
        report["raised"][part] = offset
        print(json.dumps({part: {"raised_by": round(offset, 4)}}), flush=True)

    if args.head_swap:
        return head_swap(run, h, args.faces, report, mold_mesh, sources, cutters, started,
                         head_cut if isinstance(sources.get("head"), Blend) else None)

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
