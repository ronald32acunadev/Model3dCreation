"""Signed distance fields for meshes and cutters, and a sparse block surface extractor.

Values are negative inside and positive outside. MeshField reads hollow TRELLIS.2 shells
(an outer and an inner wall) as solids by flood-filling the exterior on a grid.
"""

from collections.abc import Callable

import numpy as np
import open3d as o3d
from scipy import ndimage
from skimage.measure import marching_cubes

CHUNK = 1_000_000
# Field values closer to zero than this many grid spacings are moved to it before marching cubes.
NODE_CLEARANCE = 2e-4

Field = Callable[[np.ndarray], np.ndarray]


def face_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Unit normal of each face; degenerate faces get a zero normal."""
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)


def weld(vertices: np.ndarray, faces: np.ndarray, tolerance: float = 1e-6) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices closer than `tolerance` (UV seams) and drop faces that collapse."""
    keys = np.round(np.asarray(vertices, np.float64) / tolerance).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    welded = inverse.ravel()[np.asarray(faces, np.int64)]
    keep = (welded[:, 0] != welded[:, 1]) & (welded[:, 1] != welded[:, 2]) & (welded[:, 2] != welded[:, 0])
    return np.asarray(vertices, np.float64)[first], welded[keep]


class MeshField:
    """Signed distance to the outer surface of a closed or hollow triangle mesh."""

    def __init__(self, vertices: np.ndarray, faces: np.ndarray, cell: float):
        vertices = np.asarray(vertices, np.float64)
        faces = np.asarray(faces, np.int64)
        self.cell = float(cell)
        self.normals = face_normals(vertices, faces)
        self.scene = o3d.t.geometry.RaycastingScene()
        self.scene.add_triangles(
            o3d.core.Tensor(vertices.astype(np.float32)), o3d.core.Tensor(faces.astype(np.uint32))
        )
        self.origin = vertices.min(axis=0) - 3 * self.cell
        self.shape = np.ceil((vertices.max(axis=0) + 3 * self.cell - self.origin) / self.cell).astype(np.int64)
        count = int(np.prod(self.shape))
        distances = np.empty(count, np.float64)
        for start in range(0, count, CHUNK):
            stop = min(start + CHUNK, count)
            indices = np.stack(np.unravel_index(np.arange(start, stop), self.shape), axis=1)
            distances[start:stop] = self._distance(self.origin + (indices + 0.5) * self.cell)
        wall = (distances < 0.87 * self.cell).reshape(self.shape)
        labels, _ = ndimage.label(~wall)
        exterior = labels == labels[0, 0, 0]
        self.inside = ~exterior
        self.boundary = wall & ndimage.binary_dilation(exterior, structure=np.ones((3, 3, 3), bool))

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """Corners of the occupancy grid, which pads the mesh by three cells."""
        return self.origin, self.origin + self.shape * self.cell

    def _distance(self, points: np.ndarray) -> np.ndarray:
        tensor = o3d.core.Tensor(np.asarray(points, np.float32))
        return self.scene.compute_distance(tensor).numpy().astype(np.float64)

    def __call__(self, points: np.ndarray) -> np.ndarray:
        """Signed distance at each point."""
        points = np.asarray(points, np.float64)
        values = np.empty(len(points), np.float64)
        for start in range(0, len(points), CHUNK):
            values[start:start + CHUNK] = self._signed(points[start:start + CHUNK])
        return values

    def _signed(self, points: np.ndarray) -> np.ndarray:
        distances = self._distance(points)
        indices = np.floor((points - self.origin) / self.cell).astype(np.int64)
        in_grid = np.flatnonzero(np.all((indices >= 0) & (indices < self.shape), axis=1))
        cells = indices[in_grid]
        inside = self.inside[cells[:, 0], cells[:, 1], cells[:, 2]]
        boundary = self.boundary[cells[:, 0], cells[:, 1], cells[:, 2]]
        signs = np.ones(len(points), np.float64)
        signs[in_grid[inside & ~boundary]] = -1.0
        near = in_grid[boundary]
        if len(near):
            closest = self.scene.compute_closest_points(o3d.core.Tensor(points[near].astype(np.float32)))
            offsets = points[near] - closest["points"].numpy()
            normals = self.normals[closest["primitive_ids"].numpy().astype(np.int64)]
            signs[near] = np.where(np.einsum("ij,ij->i", offsets, normals) < 0, -1.0, 1.0)
        return signs * distances


def capsule_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray, radius: float) -> np.ndarray:
    """Signed distance to the capsule of `radius` around segment a-b."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    axis = b - a
    t = np.clip((points - a) @ axis / max(float(axis @ axis), 1e-12), 0.0, 1.0)
    return np.linalg.norm(points - (a + t[:, None] * axis), axis=1) - radius


class CutterField:
    """Region a part owns: the half-space on the part side of a plane, within a union of capsules."""

    def __init__(self, point, normal, segments: list[tuple[np.ndarray, np.ndarray, float]]):
        self.point = np.asarray(point, np.float64)
        normal = np.asarray(normal, np.float64)
        self.normal = normal / np.linalg.norm(normal)
        self.segments = [(np.asarray(a, np.float64), np.asarray(b, np.float64), float(r)) for a, b, r in segments]

    def __call__(self, points: np.ndarray) -> np.ndarray:
        """Signed distance bound to the region at each point."""
        points = np.asarray(points, np.float64)
        capsules = np.min([capsule_distance(points, a, b, r) for a, b, r in self.segments], axis=0)
        return np.maximum(capsules, -(points - self.point) @ self.normal)


def _edge_keys(grid_vertices: np.ndarray) -> np.ndarray:
    """Identify each marching-cubes vertex by the grid edge it lies on, in grid units.

    Neighbouring blocks compute the same vertex with float32 rounding differences, so rounding
    positions either misses shared vertices or collapses tiny triangles; the edge is exact.
    The edge's axis is the coordinate farthest from an integer (NODE_CLEARANCE keeps it clear).
    """
    nearest = np.round(grid_vertices)
    axis = np.argmax(np.abs(grid_vertices - nearest), axis=1)
    rows = np.arange(len(grid_vertices))
    cells = nearest.astype(np.int64)
    cells[rows, axis] = np.floor(grid_vertices[rows, axis]).astype(np.int64)
    keys = np.column_stack([cells, axis])
    # Lewiner's method adds vertices inside a cube for ambiguous cases; they are never shared.
    interior = np.count_nonzero(np.abs(grid_vertices - nearest) > 1e-3, axis=1) > 1
    keys[interior] = np.column_stack([np.arange(interior.sum()), np.zeros((interior.sum(), 2), np.int64), np.full(interior.sum(), 4)])
    return keys


def _block_nodes(origin: np.ndarray, cells: np.ndarray) -> np.ndarray:
    ranges = [origin[axis] + np.arange(cells[axis] + 1) for axis in range(3)]
    return np.stack(np.meshgrid(*ranges, indexing="ij"), axis=-1).reshape(-1, 3)


def extract_surface(
    field: Field, lo, hi, h: float, block: int = 32, batch_points: int = 2_000_000
) -> tuple[np.ndarray, np.ndarray]:
    """Zero level set of `field` inside the box [lo, hi] on a grid of spacing h, as a welded mesh."""
    lo = np.asarray(lo, np.float64)
    counts = np.ceil((np.asarray(hi, np.float64) - lo) / h).astype(np.int64)
    starts = [np.arange(0, counts[axis], block) for axis in range(3)]
    origins = np.stack(np.meshgrid(*starts, indexing="ij"), axis=-1).reshape(-1, 3)
    sizes = np.minimum(block, counts - origins)
    centers = lo + (origins + sizes / 2) * h
    coarse = np.concatenate([field(centers[s:s + batch_points]) for s in range(0, len(centers), batch_points)])
    keep = np.abs(coarse) <= 0.5 * h * np.linalg.norm(sizes, axis=1) + h
    origins, sizes = origins[keep], sizes[keep]

    vertex_parts: list[np.ndarray] = []
    face_parts: list[np.ndarray] = []
    total = 0

    def run(batch: list[tuple[np.ndarray, np.ndarray]]) -> None:
        nonlocal total
        nodes = [_block_nodes(origin, cells) for origin, cells in batch]
        values = field(lo + np.concatenate(nodes) * h)
        offset = 0
        for (origin, cells), block_nodes in zip(batch, nodes):
            volume = values[offset:offset + len(block_nodes)].reshape(tuple(cells + 1))
            offset += len(block_nodes)
            if not (volume.min() < 0.0 < volume.max()):
                continue
            # Keep every vertex off the grid nodes, so each one lies inside exactly one edge.
            volume = np.where(np.abs(volume) < NODE_CLEARANCE * h, NODE_CLEARANCE * h, volume)
            try:
                verts, faces, _, _ = marching_cubes(volume, level=0.0, allow_degenerate=False)
            except (ValueError, RuntimeError):
                continue
            vertex_parts.append(verts.astype(np.float64) + origin)
            face_parts.append(faces.astype(np.int64) + total)
            total += len(verts)

    batch: list[tuple[np.ndarray, np.ndarray]] = []
    size = 0
    for origin, cells in zip(origins, sizes):
        nodes = int(np.prod(cells + 1))
        if batch and size + nodes > batch_points:
            run(batch)
            batch, size = [], 0
        batch.append((origin, cells))
        size += nodes
    if batch:
        run(batch)

    if not vertex_parts:
        return np.empty((0, 3), np.float64), np.empty((0, 3), np.int64)
    grid_vertices = np.concatenate(vertex_parts)
    keys = _edge_keys(grid_vertices)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    vertices = lo + grid_vertices[first] * h
    faces = inverse.ravel()[np.concatenate(face_parts)]
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 2] != faces[:, 0])]
    _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    faces = faces[np.sort(first)]
    if signed_volume(vertices, faces) < 0:
        faces = faces[:, ::-1].copy()
    return vertices, faces


def signed_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    """Volume enclosed by the mesh; positive when faces wind outward."""
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)


def edge_counts(faces: np.ndarray) -> tuple[int, int]:
    """Boundary edges (used by one face) and non-manifold edges (used by more than two)."""
    if len(faces) == 0:
        return 0, 0
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1).astype(np.int64)
    _, counts = np.unique(edges[:, 0] * (int(edges.max()) + 1) + edges[:, 1], return_counts=True)
    return int(np.sum(counts == 1)), int(np.sum(counts > 2))
