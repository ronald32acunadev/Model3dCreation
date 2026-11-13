"""The chin finder on analytic heads: a face on a neck, with and without armour around it."""

import numpy as np
import pytest

from landmarks import find_face, plane_height

HEAD_Y = 0.42
HEAD_R = 0.07
NOSE = np.array([0.0, 0.42, 0.075])
CHIN = np.array([0.0, 0.365, 0.045])
NECK_R = 0.03
SHOULDERS_Y = 0.30
JOINT_Y = 0.34


def sphere(points, center, radius):
    return np.linalg.norm(points - center, axis=1) - radius


def bust(points: np.ndarray) -> np.ndarray:
    """Head with a nose and a chin, a neck, and a wide chest below SHOULDERS_Y (negative inside)."""
    head = sphere(points, np.array([0.0, HEAD_Y, 0.0]), HEAD_R)
    nose = sphere(points, NOSE, 0.015)
    chin = sphere(points, CHIN, 0.025)
    radial = np.hypot(points[:, 0], points[:, 2])
    neck = np.maximum(radial - NECK_R, np.maximum(points[:, 1] - HEAD_Y, SHOULDERS_Y - points[:, 1]))
    chest = np.maximum(np.abs(points[:, 0]) - 0.18, np.maximum(np.abs(points[:, 2]) - 0.08, points[:, 1] - SHOULDERS_Y))
    chest = np.maximum(chest, 0.2 - points[:, 1])
    return np.min([head, nose, chin, neck, chest], axis=0)


def armoured(points: np.ndarray) -> np.ndarray:
    """The bust inside a tall collar ring, clear of the chin, and two pauldrons that reach the ears."""
    radial = np.hypot(points[:, 0], points[:, 2])
    collar = np.max([0.08 - radial, radial - 0.11, points[:, 1] - 0.37, SHOULDERS_Y - 0.01 - points[:, 1]], axis=0)
    pauldrons = np.min([sphere(points, np.array([side * 0.19, 0.33, 0.0]), 0.08) for side in (-1, 1)], axis=0)
    return np.min([bust(points), collar, pauldrons], axis=0)


def test_finds_the_chin_between_the_nose_and_the_neck():
    face = find_face(bust, 0.2, HEAD_Y + HEAD_R, JOINT_Y)
    assert face.nose_y == pytest.approx(NOSE[1], abs=0.01)
    assert CHIN[1] - 0.03 <= face.chin_y <= CHIN[1]
    assert SHOULDERS_Y < face.throat_y < face.chin_y


def fringe(points: np.ndarray) -> np.ndarray:
    """The bust with a quiff of hair over the forehead that juts out past the nose."""
    quiff = sphere(points, np.array([0.0, 0.47, 0.07]), 0.025)
    return np.minimum(bust(points), quiff)


def test_hair_jutting_past_the_nose_is_not_taken_for_the_nose():
    face = find_face(fringe, 0.2, 0.5, JOINT_Y)
    assert face.nose_y == pytest.approx(NOSE[1], abs=0.01)
    assert CHIN[1] - 0.03 <= face.chin_y <= CHIN[1]


def test_a_search_box_taller_than_the_head_finds_the_same_face():
    tight = find_face(bust, 0.2, HEAD_Y + HEAD_R, JOINT_Y)
    loose = find_face(bust, 0.2, HEAD_Y + HEAD_R + 0.08, JOINT_Y)
    assert (loose.nose_y, loose.chin_y, loose.throat_y) == pytest.approx((tight.nose_y, tight.chin_y, tight.throat_y), abs=0.005)


def test_armour_around_the_head_does_not_move_the_chin():
    plain = find_face(bust, 0.2, HEAD_Y + HEAD_R, JOINT_Y)
    face = find_face(armoured, 0.2, HEAD_Y + HEAD_R, JOINT_Y)
    assert face.chin_y == pytest.approx(plain.chin_y, abs=0.005)
    assert face.nose_y == pytest.approx(plain.nose_y, abs=0.005)
    assert face.throat_y < face.chin_y


def collared(points: np.ndarray) -> np.ndarray:
    """The bust whose jaw sits in a wide collar plate it touches (seen on an armoured character)."""
    plate = np.max([np.abs(points[:, 0]) - 0.15, np.abs(points[:, 2]) - 0.1,
                    points[:, 1] - 0.355, 0.33 - points[:, 1]], axis=0)
    return np.minimum(bust(points), plate)


def test_a_collar_joined_to_the_jaw_is_not_taken_for_the_face():
    face = find_face(collared, 0.2, HEAD_Y + HEAD_R, JOINT_Y)
    assert face.nose_y == pytest.approx(NOSE[1], abs=0.01)
    assert face.chin_y >= 0.355


def test_a_part_with_nothing_above_its_joint_is_refused():
    ball = lambda points: sphere(points, np.array([0.0, 0.25, 0.0]), 0.04)
    with pytest.raises(ValueError):
        find_face(ball, 0.2, 0.29, 0.36)


def test_plane_height_follows_a_tilted_plane():
    plane = {"point": [0.0, 0.4, 0.0], "normal": [0.0, 0.8, 0.6]}
    assert plane_height(plane, 0.0, 0.0) == pytest.approx(0.4)
    assert plane_height(plane, 0.0, 0.1) == pytest.approx(0.4 - 0.6 * 0.1 / 0.8)
