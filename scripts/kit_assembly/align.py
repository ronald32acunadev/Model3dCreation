"""Move each generated part onto its region of the mold: bounding boxes first, then scaled ICP.

`python align.py RUN_DIR` reads mold.glb, mold_labels.npy, rig.json and parts/<part>.glb, and
writes aligned/<part>.glb (textures kept) and align.json (see README.md).

A bust (head and shoulders) is then moved, not turned, onto the old head alone: the bust's head
and the mold's old head, each read from its own horizontal sections (landmarks.py), so a collar or
pauldrons around the head do not pull it (they held the armoured character's new head 1.6 cm off).
"""

import json
import sys
from pathlib import Path

import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree

SAMPLES = 40_000
HEAD_SAMPLES = 20_000
SHIFT_DISTANCES = (0.04, 0.02, 0.01)  # correspondence limits of the head shift, coarse to fine
SHIFT_ITERATIONS = 30
HEAD_BELOW = 0.02  # the heads are compared from this far below the bust's throat
ICP_DISTANCES = (0.04, 0.02, 0.01, 0.005)  # glTF units; the body is about 1.0 tall


def region_points(mold: trimesh.Trimesh, labels: np.ndarray, part: int, above: float | None = None) -> np.ndarray:
    """Surface samples of the mold faces whose corners mostly belong to `part`, or, for a bust,
    of every face whose center is above the height `above`."""
    if above is None:
        corner = labels[mold.faces]
        face_labels = np.where(corner[:, 1] == corner[:, 2], corner[:, 1], corner[:, 0])
        selected = np.flatnonzero(face_labels == part)
    else:
        selected = np.flatnonzero(mold.triangles_center[:, 1] > above)
    region = mold.submesh([selected], append=True)
    points, _ = trimesh.sample.sample_surface(region, SAMPLES, seed=0)
    return np.asarray(points)


def box_fit(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Similarity transform matching the front-view (x, y) size and the box centers."""
    source_size = np.ptp(source, axis=0)
    target_size = np.ptp(target, axis=0)
    scale = float(np.mean(target_size[:2] / source_size[:2]))
    center = (source.min(axis=0) + source.max(axis=0)) / 2
    matrix = np.eye(4)
    matrix[:3, :3] *= scale
    matrix[:3, 3] = (target.min(axis=0) + target.max(axis=0)) / 2 - scale * center
    return matrix


def icp(source: np.ndarray, target: np.ndarray, start: np.ndarray, scaling: bool) -> tuple[np.ndarray, float]:
    """Point-to-point ICP, from coarse to fine correspondence distances."""
    source_cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(source))
    target_cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(target))
    estimation = o3d.pipelines.registration.TransformationEstimationPointToPoint(with_scaling=scaling)
    criteria = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=60)
    matrix, fitness = start, 0.0
    for distance in ICP_DISTANCES:
        result = o3d.pipelines.registration.registration_icp(source_cloud, target_cloud, distance, matrix, estimation, criteria)
        if result.fitness > 0:
            matrix, fitness = np.asarray(result.transformation), float(result.fitness)
    return matrix, fitness


def transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def chamfer(source: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    """Mean nearest distance source to target, and target to source."""
    return float(cKDTree(target).query(source)[0].mean()), float(cKDTree(source).query(target)[0].mean())


def best_fit(source: np.ndarray, target: np.ndarray) -> tuple[str, np.ndarray, float, dict]:
    """The box fit, rigid ICP, or scaled ICP with the lowest two-way mean distance.

    Scaled ICP can collapse a part into a corner of its region: the part's distance to the mold
    then looks small while most of the region is uncovered, which only the reverse distance shows.
    """
    start = box_fit(source, target)
    rigid, rigid_fitness = icp(source, target, start, scaling=False)
    scaled, scaled_fitness = icp(source, target, rigid, scaling=True)
    candidates = {"box": (start, 0.0), "rigid": (rigid, rigid_fitness), "scaled": (scaled, scaled_fitness)}
    scores = {name: chamfer(transform(source, m), target) for name, (m, _) in candidates.items()}
    name = min(scores, key=lambda n: sum(scores[n]))
    return name, candidates[name][0], candidates[name][1], scores


def shift_to_head(source: np.ndarray, target: np.ndarray, start: np.ndarray) -> tuple[str, np.ndarray, dict]:
    """The start, or the start moved (never turned or scaled) onto the target head, whichever has
    the lowest two-way mean distance between the heads. `source` and `target` are head surface
    points; `source` is in the part's own coordinates, placed by `start`.

    Turning the bust to the old head's pose (the armoured character's was turned 17 degrees) also
    turned its face away from the front, where the face is read (landmarks.py); the sculpted
    head keeps the bust's facing, which is the input image's.
    """
    tree = cKDTree(target)
    matrix = start.copy()
    for distance in SHIFT_DISTANCES:
        for _ in range(SHIFT_ITERATIONS):
            placed = transform(source, matrix)
            gaps, nearest = tree.query(placed, distance_upper_bound=distance)
            close = np.isfinite(gaps)
            if close.sum() < 10:
                break
            step = (target[nearest[close]] - placed[close]).mean(axis=0)
            matrix[:3, 3] += step
            if np.linalg.norm(step) < 1e-5:
                break
    candidates = {"start": start, "shifted": matrix}
    scores = {name: chamfer(transform(source, m), target) for name, m in candidates.items()}
    name = min(scores, key=lambda n: sum(scores[n]))
    return name, candidates[name], scores


def head_points(run: Path, rig: dict, mold: trimesh.Trimesh, mesh: trimesh.Trimesh, matrix: np.ndarray,
                source: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The bust's head points (rows of `source`, in its own coordinates) and the mold's old head
    points, both from the throat of the bust placed by `matrix` up."""
    from kit import ENCLOSURE, mesh_field
    from landmarks import find_face, head_members

    joint_y = float(rig["joints"]["head"]["point"][1])
    lowest = json.loads((run / "crops.json").read_text())["parts"]["head_bust"]["y_cut"]
    placed = transform(np.asarray(mesh.vertices), matrix)
    bust_field = mesh_field(placed, np.asarray(mesh.faces), enclosure=ENCLOSURE)
    top = float(placed[:, 1].max())
    face = find_face(bust_field, lowest, top, joint_y)
    bottom = face.throat_y - HEAD_BELOW
    moved = transform(source, matrix)
    keep = moved[:, 1] >= bottom
    keep[keep] = head_members(bust_field, face.axis, bottom, top + 0.004, joint_y, moved[keep])
    mold_field = mesh_field(np.asarray(mold.vertices), np.asarray(mold.faces), enclosure=ENCLOSURE)
    target, _ = trimesh.sample.sample_surface(mold, SAMPLES * 4, seed=1)
    target = np.asarray(target)
    target = target[(target[:, 1] >= bottom) & (target[:, 1] <= top + 0.05)]
    mold_top = float(target[:, 1].max()) + 0.004 if len(target) else top
    target = target[head_members(mold_field, face.axis, bottom, mold_top, joint_y, target)]
    return source[keep], target


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: align.py RUN_DIR", file=sys.stderr)
        return 2
    run = Path(argv[1])
    rig = json.loads((run / "rig.json").read_text())
    mold = trimesh.load(run / "mold.glb", force="mesh")
    labels = np.load(run / "mold_labels.npy")
    (run / "aligned").mkdir(exist_ok=True)
    report = json.loads((run / "align.json").read_text()) if (run / "align.json").exists() else {}
    for path in sorted((run / "parts").glob("*.glb")):
        part = path.stem
        if part in report and (run / "aligned" / path.name).exists():
            continue
        base = part.split("@")[0]
        bust = base.endswith("_bust")
        index = rig["parts"].index(base.removesuffix("_bust"))
        scene = trimesh.load(path)
        mesh = scene.dump(concatenate=True) if isinstance(scene, trimesh.Scene) else scene
        source, _ = trimesh.sample.sample_surface(mesh, SAMPLES, seed=0)
        above = json.loads((run / "crops.json").read_text())["parts"]["head_bust"]["y_cut"] if bust else None
        target = region_points(mold, labels, index, above)
        method, matrix, fitness, scores = best_fit(np.asarray(source), target)
        head = None
        if bust:
            try:
                head_source, head_target = head_points(run, rig, mold, mesh, matrix, np.asarray(source))
            except ValueError as error:
                head = {"error": str(error)}
            else:
                if len(head_source) and len(head_target):
                    refined, matrix, head_scores = shift_to_head(head_source, head_target, matrix)
                    head = {"method": refined, "scores": head_scores,
                            "mean_distance": head_scores[refined][0], "reverse_mean_distance": head_scores[refined][1],
                            "start_mean_distance": head_scores["start"][0],
                            "start_reverse_mean_distance": head_scores["start"][1]}
        distances, _ = cKDTree(target).query(transform(np.asarray(source), matrix))
        scene.apply_transform(matrix)
        scene.export(run / "aligned" / f"{part}.glb")
        report[part] = {
            "method": method,
            "matrix": matrix.tolist(),
            "scale": float(np.cbrt(np.linalg.det(matrix[:3, :3]))),
            "mean_distance": float(distances.mean()),
            "median_distance": float(np.median(distances)),
            "reverse_mean_distance": scores[method][1],
            "scores": scores,
            "fitness": fitness,
            "head": head,
        }
        summary = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in report[part].items()
                   if k not in ("matrix", "scores", "head")}
        if head is not None:
            summary["head"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in head.items() if k != "scores"}
        summary["scores"] = {n: [round(a, 4), round(b, 4)] for n, (a, b) in scores.items()}
        print(json.dumps({part: summary}), flush=True)
    (run / "align.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
