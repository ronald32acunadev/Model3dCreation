"""Split a mold's surface into kit parts by geodesic distance from each part's bones.

UniRig's skin weights bleed where body parts touch (a hand resting on a thigh takes the thigh),
and the nearest bone is wrong where a limb hangs against the body. Vertices where both agree are
kept as seeds; every other vertex joins the seed region that reaches it first over the surface.
"""

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from sdf import capsule_distance, weld


def part_segments(rig: dict) -> list[tuple[int, np.ndarray, np.ndarray]]:
    """(part index, head, tail) of every bone, the torso's included."""
    segments = [(0, np.asarray(a), np.asarray(b)) for a, b in rig.get("torso_segments", [])]
    for index, part in enumerate(rig["parts"][1:], start=1):
        segments += [(index, np.asarray(a), np.asarray(b)) for a, b in rig["joints"][part]["segments"]]
    return segments


def nearest_bone_labels(points: np.ndarray, rig: dict) -> np.ndarray:
    """Part of the bone segment closest to each point."""
    segments = part_segments(rig)
    distances = np.stack([capsule_distance(points, a, b, 0.0) for _, a, b in segments])
    return np.array([part for part, _, _ in segments], np.int8)[np.argmin(distances, axis=0)]


def geodesic_labels(vertices: np.ndarray, faces: np.ndarray, rig: dict, skin_labels: np.ndarray) -> np.ndarray:
    """Part index of each input vertex; `skin_labels` are the rig's dominant-weight parts per vertex."""
    if len(rig["parts"]) == 1:
        return np.zeros(len(vertices), np.int8)
    welded, welded_faces = weld(vertices, faces)
    _, owner = cKDTree(welded).query(vertices)
    skin = np.zeros(len(welded), np.int8)
    skin[owner] = skin_labels
    seeds = np.flatnonzero(skin == nearest_bone_labels(welded, rig))
    edges = np.concatenate([welded_faces[:, [0, 1]], welded_faces[:, [1, 2]], welded_faces[:, [2, 0]]])
    lengths = np.linalg.norm(welded[edges[:, 0]] - welded[edges[:, 1]], axis=1)
    graph = coo_matrix((lengths, (edges[:, 0], edges[:, 1])), shape=(len(welded),) * 2).tocsr()
    _, _, sources = dijkstra(graph, directed=False, indices=seeds, min_only=True, return_predecessors=True)
    labels = skin.copy()
    reached = sources >= 0
    labels[reached] = skin[sources[reached]]
    return labels[owner]
