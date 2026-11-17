"""Split a model into texturing regions and blend their textures back into the model's own one.

Runs in the engine's interpreter (numpy, scipy, trimesh). A region is one skeleton part of the
model (head, torso, each arm, each leg; `mold_labels.npy` from the head pass) grown by BLEND into
its neighbours, so that two regions overlap where they meet. Each region keeps the model's own
UVs, so its new texture lands texel for texel in the model's texture. Where regions overlap, every
texel takes a mix weighted by how deep it lies in each region's own part: the colors fade from one
region to the next over BLEND instead of meeting at a seam.
"""

from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from texture import Texels, texels

BLEND = 0.03  # glTF units (body about 1.0 tall) over which one region's color fades into the next


@dataclass
class Region:
    name: str
    faces: np.ndarray  # model faces in the region, its own part plus the BLEND margin
    mesh: trimesh.Trimesh  # those faces with the model's UVs


def face_labels(mesh: trimesh.Trimesh, vertex_labels: np.ndarray) -> np.ndarray:
    """A part per face: the label two or three of its corners share (the first corner's otherwise)."""
    corner = vertex_labels[mesh.faces]
    return np.where(corner[:, 1] == corner[:, 2], corner[:, 1], corner[:, 0])


def submesh(mesh: trimesh.Trimesh, faces: np.ndarray) -> trimesh.Trimesh:
    """The faces as their own mesh, keeping the model's UVs and material."""
    used, inverse = np.unique(mesh.faces[faces], return_inverse=True)
    visual = trimesh.visual.TextureVisuals(uv=np.asarray(mesh.visual.uv)[used], material=mesh.visual.material)
    return trimesh.Trimesh(np.asarray(mesh.vertices)[used], inverse.reshape(-1, 3), process=False, visual=visual)


def split(mesh: trimesh.Trimesh, labels: np.ndarray, names: list[str]) -> list[Region]:
    """One region per part present in `labels` (per face, indices into `names`), grown by BLEND."""
    centers = np.asarray(mesh.triangles_center)
    regions = []
    for index, name in enumerate(names):
        own = np.flatnonzero(labels == index)
        if len(own) == 0:
            continue
        distance, _ = cKDTree(centers[own]).query(centers, distance_upper_bound=BLEND)
        faces = np.flatnonzero(np.isfinite(distance))
        regions.append(Region(name, faces, submesh(mesh, faces)))
    return regions


def weights(points: np.ndarray, centers: np.ndarray, labels: np.ndarray, count: int) -> np.ndarray:
    """Per point and part, a smooth weight that is 1 inside the part and fades to 0 at BLEND
    outside it; rows sum to 1 (a point far from every part takes its nearest part)."""
    result = np.zeros((len(points), count), np.float32)
    for index in range(count):
        own = centers[labels == index]
        if len(own) == 0:
            continue
        distance, _ = cKDTree(own).query(points, distance_upper_bound=BLEND)
        t = np.clip(1 - np.nan_to_num(distance, posinf=BLEND) / BLEND, 0, 1)
        result[:, index] = t * t * (3 - 2 * t)
    empty = result.sum(axis=1) == 0
    if empty.any():
        _, nearest = cKDTree(centers).query(points[empty])
        result[np.flatnonzero(empty), labels[nearest]] = 1.0
    return result / result.sum(axis=1, keepdims=True)


def blend(mesh: trimesh.Trimesh, labels: np.ndarray, count: int, base: np.ndarray,
          painted: dict[int, np.ndarray]) -> np.ndarray:
    """The model's texture (H x W x 4, floats) with each part's new texture (`painted`, same size)
    mixed in by `weights`; parts without a new texture keep the model's colors."""
    found: Texels = texels(mesh, (base.shape[1], base.shape[0]))
    mix = weights(found.points, np.asarray(mesh.triangles_center), labels, count)
    colors = np.zeros((len(found.rows), 3), np.float32)
    for index in range(count):
        source = painted.get(index, base)
        colors += mix[:, index:index + 1] * source[found.rows, found.columns, :3]
    result = base.copy()
    result[found.rows, found.columns, :3] = colors
    return result
