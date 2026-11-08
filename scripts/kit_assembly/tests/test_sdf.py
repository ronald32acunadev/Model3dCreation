import numpy as np
import pytest
import trimesh
from trimesh.creation import icosphere, capsule
from sdf import MeshField, CutterField, extract_surface, signed_volume, edge_counts


def _volume(field, lo, hi, h):
    vertices, faces = extract_surface(field, lo, hi, h)
    return signed_volume(vertices, faces)


def test_mesh_field_signs_on_a_sphere():
    sphere = icosphere(subdivisions=4, radius=0.3)
    field = MeshField(sphere.vertices, sphere.faces, cell=0.01)
    points = np.array([
        [0., 0., 0.],
        [0.5, 0., 0.],
        [0.29, 0., 0.],
        [0.31, 0., 0.]
    ])
    values = field(points)
    assert values[0] < -0.28
    assert abs(values[1] - 0.2) < 0.01
    assert values[2] < 0
    assert values[3] > 0


def test_mesh_field_treats_a_hollow_shell_as_solid():
    outer = icosphere(4, 0.3)
    inner = icosphere(4, 0.25)
    vertices = np.vstack([outer.vertices, inner.vertices])
    faces = np.vstack([outer.faces, inner.faces[:, ::-1] + len(outer.vertices)])
    field = MeshField(vertices, faces, cell=0.01)
    points = np.array([
        [0., 0., 0.],
        [0.27, 0., 0.],
        [0.35, 0., 0.]
    ])
    values = field(points)
    assert values[0] < 0
    assert values[1] < 0
    assert values[2] > 0


def test_cut_and_cap_gives_a_closed_half_capsule():
    capsule_mesh = capsule(height=0.6, radius=0.1)
    mesh_field = MeshField(capsule_mesh.vertices, capsule_mesh.faces, cell=0.01)
    cutter = CutterField(point=(0, 0, 0), normal=(0, 0, 1), segments=[((0, 0, 0), (0, 0, 1), 0.3)])
    lo, hi = mesh_field.bounds
    full_volume = capsule_mesh.volume

    # Extract full capsule
    full_vertices, full_faces = extract_surface(mesh_field, lo, hi, h=0.005)
    full_vol = signed_volume(full_vertices, full_faces)

    # Extract cut piece
    cut_field = lambda p: np.maximum(mesh_field(p), cutter(p))
    piece_vertices, piece_faces = extract_surface(cut_field, lo, hi, h=0.005)
    piece_vol = signed_volume(piece_vertices, piece_faces)
    boundary_edges, nonmanifold_edges = edge_counts(piece_faces)

    assert boundary_edges == 0
    assert nonmanifold_edges == 0
    assert piece_vol > 0
    assert abs(piece_vol - full_volume / 2) < 0.05 * full_volume / 2

    # Check that all z coordinates are at least -0.005
    assert np.all(piece_vertices[:, 2] >= -0.005)

    # Verify full capsule volume
    assert abs(full_vol - full_volume) < 0.05 * full_volume


def test_two_capped_halves_fuse_into_one_closed_surface():
    capsule_mesh = capsule(height=0.6, radius=0.1)
    mesh_field = MeshField(capsule_mesh.vertices, capsule_mesh.faces, cell=0.01)
    upper = CutterField((0, 0, 0), (0, 0, 1), [((0, 0, 0), (0, 0, 1), 0.3)])
    lower = CutterField((0, 0, 0), (0, 0, -1), [((0, 0, 0), (0, 0, -1), 0.3)])

    def fused_field(p):
        m = mesh_field(p)
        u = upper(p)
        l = lower(p)
        return np.minimum(np.maximum(m, u), np.maximum(m, l))

    lo, hi = mesh_field.bounds
    vertices, faces = extract_surface(fused_field, lo, hi, h=0.005)
    volume = signed_volume(vertices, faces)

    boundary_edges, nonmanifold_edges = edge_counts(faces)
    assert boundary_edges == 0
    assert nonmanifold_edges == 0
    assert abs(volume - capsule_mesh.volume) < 0.05 * capsule_mesh.volume


def test_a_rough_surface_over_many_blocks_is_closed():
    def bumpy_sphere(p):
        bumps = 0.05 * np.sin(80 * p[:, 0]) * np.sin(80 * p[:, 1]) * np.sin(80 * p[:, 2])
        # Dense bumps give ambiguous cubes across many small blocks. Dividing by the
        # slope bound (1 + 0.05 * 80 * sqrt(3) < 8) keeps the extractor's block skipping valid.
        return (np.linalg.norm(p, axis=1) - 0.3 + bumps) / 8

    vertices, faces = extract_surface(bumpy_sphere, np.full(3, -0.4), np.full(3, 0.4), h=0.01, block=4)

    assert edge_counts(faces) == (0, 0)
    assert signed_volume(vertices, faces) > 0
