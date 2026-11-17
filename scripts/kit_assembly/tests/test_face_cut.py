"""The head swap's cut comes from the sculpted head's face; it keeps the sculpted head's own faces
and removes only the old head's, never a collar or pauldrons beside it."""

import numpy as np
import pytest
import trimesh

from kit import BLEND, BLEND_MIN, face_cut
from landmarks import find_face, plane_height
from sdf import CutterField, extract_surface
from test_landmarks import HEAD_Y, JOINT_Y, SHOULDERS_Y, armoured, bust, sphere

H = 1 / 512
BONE_TOP = 0.44
BOX = (np.array([-0.32, 0.2, -0.32]), np.array([0.32, 0.56, 0.32]))


class Bounded:
    """An analytic field with the bounds kit.py reads from its mesh fields."""

    def __init__(self, function):
        self.function, self.lo, self.hi = function, BOX[0], BOX[1]

    def __call__(self, points):
        return self.function(points)


def mesh(function) -> trimesh.Trimesh:
    vertices, faces = extract_surface(function, *BOX, 0.004)
    return trimesh.Trimesh(vertices, faces, process=False)


def joint_cutter(radius: float = 0.3) -> CutterField:
    # The mold's head label spans pauldrons and collar: its capsule is as wide as the shoulders.
    return CutterField([0.0, JOINT_Y, 0.0], [0.0, 1.0, 0.0],
                       [(np.array([0.0, JOINT_Y, 0.0]), np.array([0.0, BONE_TOP, 0.0]), radius)])


def cut_for(part, mold):
    part_mesh, mold_mesh = mesh(part), mesh(mold)
    head_cut = face_cut(joint_cutter(), Bounded(part), Bounded(mold), part_mesh, mold_mesh, JOINT_Y, 0.2, H)
    return head_cut, part_mesh, mold_mesh


def test_the_cut_lands_below_the_chin_of_the_new_head_inside_armour():
    head_cut, _, _ = cut_for(bust, armoured)
    cut = head_cut.cutter
    chin = find_face(Bounded(bust), 0.2, 0.56, JOINT_Y)
    at_chin = plane_height({"point": cut.point, "normal": cut.normal}, chin.axis[0], chin.chin_z)
    assert SHOULDERS_Y < at_chin <= chin.chin_y - BLEND_MIN
    assert BLEND_MIN <= head_cut.width <= BLEND
    assert at_chin + head_cut.width <= chin.chin_y + 1e-9


def test_only_the_old_head_is_removed_never_the_collar_or_pauldrons():
    head_cut, _, mold_mesh = cut_for(bust, armoured)
    centers = mold_mesh.triangles_center
    removed = centers[head_cut.remove_mold]
    radial = np.hypot(removed[:, 0], removed[:, 2])
    assert len(removed)
    assert (np.abs(removed[:, 0]) < 0.11).all()  # pauldrons start 11 cm from the axis
    assert not ((radial > 0.08) & (removed[:, 1] < 0.37)).any()  # the collar ring
    assert head_cut.remove_mold[centers[:, 1] > HEAD_Y + 0.05].all()  # the old crown goes


def test_the_sculpted_head_keeps_its_own_faces_above_the_band_only():
    head_cut, part_mesh, _ = cut_for(bust, armoured)
    centers = part_mesh.triangles_center
    height = (centers - head_cut.cutter.point) @ head_cut.cutter.normal
    assert head_cut.keep_part[centers[:, 1] > HEAD_Y].all()
    assert not head_cut.keep_part[height < head_cut.width].any()


def eared(points: np.ndarray) -> np.ndarray:
    """The bust with two ears whose tips stand well above the head bone's end."""
    ears = np.min([sphere(points, np.array([side * 0.05, 0.5, 0.0]), 0.03) for side in (-1, 1)], axis=0)
    return np.minimum(bust(points), ears)


def test_ears_above_the_bone_are_kept_and_the_band_stays_neck_sized():
    head_cut, part_mesh, _ = cut_for(eared, armoured)
    centers = part_mesh.triangles_center
    assert head_cut.keep_part[centers[:, 1] > 0.51].all()
    assert head_cut.cutter.segments[0][2] < 0.11  # the band never reaches the pauldrons


def test_a_hollow_neck_is_refused():
    def shell(points):
        return np.abs(bust(points)) - 0.003  # a 6 mm wall with nothing inside

    with pytest.raises(ValueError):
        cut_for(shell, armoured)


def test_a_thin_piece_of_old_head_joined_to_the_removed_faces_goes_too():
    from kit import absorb_strays

    strip = trimesh.creation.box(extents=(1.0, 0.01, 0.01))
    strip = trimesh.Trimesh(*trimesh.remesh.subdivide_to_size(strip.vertices, strip.faces, 0.02), process=True)
    far = trimesh.creation.box(extents=(0.01, 0.01, 0.01)).apply_translation((3.0, 0.0, 0.0))
    mesh = trimesh.util.concatenate([strip, far])
    centers = mesh.triangles_center
    removed = centers[:, 0] < 0.45  # most of the strip
    tip = (centers[:, 0] >= 0.45) & (centers[:, 0] < 0.5)  # the strip's end, joined to it
    lone = centers[:, 0] > 2.0  # a separate piece, like a pauldron
    result = absorb_strays(mesh, removed, tip | lone, share=0.5)
    assert result[tip].all()
    assert not result[lone].any()
    assert not result[(centers[:, 0] >= 0.5) & (centers[:, 0] < 2.0)].any()  # not a candidate


def test_a_single_face_piece_of_old_head_is_absorbed_too():
    from kit import absorb_strays

    mesh = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], [[0, 1, 2], [1, 3, 2]], process=False)
    removed = np.array([True, False])
    result = absorb_strays(mesh, removed, np.array([False, True]), share=2.0)
    assert result.tolist() == [True, True]
