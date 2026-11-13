"""A bust is moved onto the old head alone, so a collar around the head cannot hold it off."""

import numpy as np

from align import shift_to_head, transform


def sphere_points(center, radius, count=4000, seed=0):
    rng = np.random.default_rng(seed)
    directions = rng.normal(size=(count, 3))
    return np.asarray(center) + radius * directions / np.linalg.norm(directions, axis=1, keepdims=True)


def turned(degrees: float) -> np.ndarray:
    angle = np.radians(degrees)
    matrix = np.eye(4)
    matrix[:3, :3] = [[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]]
    return matrix


def test_the_head_moves_onto_the_old_head_from_a_fit_held_by_a_collar():
    old_head = sphere_points([0.0, 0.42, 0.0], 0.07, seed=1)
    new_head = sphere_points([0.0, 0.0, 0.0], 0.07, seed=2)  # in the bust's own coordinates
    start = turned(10.0)
    start[:3, 3] = [0.012, 0.43, -0.01]  # the whole-bust fit left the head 1.6 cm off
    method, matrix, scores = shift_to_head(new_head, old_head, start)
    assert method == "shifted"
    assert sum(scores["shifted"]) < sum(scores["start"])
    assert np.linalg.norm(transform(new_head, matrix).mean(axis=0) - [0.0, 0.42, 0.0]) < 0.002
    assert np.allclose(matrix[:3, :3], start[:3, :3])  # never turned or scaled


def test_a_fit_already_on_the_head_stays():
    old_head = sphere_points([0.0, 0.42, 0.0], 0.07, seed=1)
    new_head = sphere_points([0.0, 0.0, 0.0], 0.07, seed=2)
    start = np.eye(4)
    start[:3, 3] = [0.0, 0.42, 0.0]
    _, matrix, _ = shift_to_head(new_head, old_head, start)
    assert np.linalg.norm(matrix[:3, 3] - start[:3, 3]) < 0.002
