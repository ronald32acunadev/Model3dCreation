import numpy as np

from model3d_engine.stages.smooth_skin import MAX_PASSES, passes_for, smooth_weights


def grid(side: int, spacing: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Vertices of a side x side lattice (row-major) and two triangles per cell."""
    rows, columns = np.divmod(np.arange(side * side), side)
    points = np.column_stack([columns * spacing, rows * spacing, np.zeros(side * side)])
    faces = []
    for row in range(side - 1):
        for column in range(side - 1):
            corner = row * side + column
            above = corner + side
            faces.append((corner, corner + 1, above + 1))
            faces.append((corner, above + 1, above))
    return points.astype(np.float64), np.array(faces, np.int64)


def edge_steps(weights: np.ndarray, faces: np.ndarray) -> np.ndarray:
    pairs = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    return np.abs(weights[pairs[:, 0]] - weights[pairs[:, 1]]).max(axis=1)


def median_edge(points: np.ndarray, faces: np.ndarray) -> float:
    pairs = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    return float(np.median(np.linalg.norm(points[pairs[:, 0]] - points[pairs[:, 1]], axis=1)))


def test_steps_become_smaller():
    points, faces = grid(20)
    columns = np.tile(np.arange(20), 20)
    weights = np.zeros((400, 2), np.float32)
    weights[:, 0] = columns < 10
    weights[:, 1] = columns >= 10
    before = edge_steps(weights, faces)
    after = edge_steps(smooth_weights(points, faces, weights, 3.0), faces)
    assert np.percentile(after, 99) < np.percentile(before, 99)
    assert after.max() < before.max()


def test_at_most_four_influences_summing_to_one():
    points, faces = grid(20)
    columns = np.tile(np.arange(20), 20)
    weights = np.zeros((400, 8), np.float32)
    weights[np.arange(400), columns % 8] = 1.0
    smoothed = smooth_weights(points, faces, weights, 5.0)
    assert ((smoothed > 0).sum(axis=1) <= 4).all()
    assert np.allclose(smoothed.sum(axis=1), 1.0, atol=1e-5)


def test_coincident_copies_keep_equal_weights():
    points, faces = grid(20)
    original = 10 * 20 + 10
    copy = len(points)
    points = np.vstack([points, points[original]])
    faces = faces.copy()
    face = np.nonzero((faces == original).any(axis=1))[0][0]
    faces[face][faces[face] == original] = copy
    weights = np.zeros((len(points), 2), np.float32)
    weights[:400, 0] = np.tile(np.arange(20), 20) < 10
    weights[:400, 1] = np.tile(np.arange(20), 20) >= 10
    weights[original] = [1.0, 0.0]
    weights[copy] = [0.0, 1.0]
    smoothed = smooth_weights(points, faces, weights, 3.0)
    assert np.array_equal(smoothed[original], smoothed[copy])


def test_vertices_without_weights_stay_at_zero():
    points, faces = grid(20)
    weights = np.zeros((400, 2), np.float32)
    weights[:, 0] = np.tile(np.arange(20), 20) < 10
    weights[:, 1] = np.tile(np.arange(20), 20) >= 10
    start = len(points)
    points = np.vstack([points, [[100.0, 100.0, 0.0], [101.0, 100.0, 0.0], [100.0, 101.0, 0.0]]])
    faces = np.vstack([faces, [[start, start + 1, start + 2]]])
    weights = np.vstack([weights, np.zeros((3, 2), np.float32)])
    smoothed = smooth_weights(points, faces, weights, 3.0)
    assert np.array_equal(smoothed[start:], np.zeros((3, 2), np.float32))
    assert np.isfinite(smoothed).all()


def test_input_is_not_changed():
    points, faces = grid(20)
    weights = np.zeros((400, 2), np.float32)
    weights[:, 0] = np.tile(np.arange(20), 20) < 10
    weights[:, 1] = np.tile(np.arange(20), 20) >= 10
    before = weights.copy()
    smooth_weights(points, faces, weights, 3.0)
    assert np.array_equal(weights, before)


def test_passes_follow_radius_not_density():
    coarse_points, coarse_faces = grid(6, 4.0)
    dense_points, dense_faces = grid(21, 1.0)
    coarse = passes_for(median_edge(coarse_points, coarse_faces), 3.0)
    dense = passes_for(median_edge(dense_points, dense_faces), 3.0)
    assert dense > coarse
    assert passes_for(1.0, 2.0) == 5
    assert passes_for(0.01, 100.0) == MAX_PASSES
