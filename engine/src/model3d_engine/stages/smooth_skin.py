"""Smooth a rigged model's skin weights over its surface, so the skin does not tear when a joint bends.

UniRig's weights come in steps: each vertex takes its bone weight from the vertices it was sampled
from. On a dense mesh a small step moves neighbouring vertices further apart than their edge length,
which tears the surface. This script averages every vertex's weights with its welded neighbours over
a radius, then keeps the largest four influences per vertex, as glTF carries them.

Usage: python smooth_skin.py SOURCE.glb TARGET.glb

Run it with UniRig's interpreter, which has bpy but not model3d_engine, so this module must not
import model3d_engine.
"""

import os
import sys
from pathlib import Path

import numpy as np
from scipy import sparse

SPREAD = 0.015  # how far weights spread, as a fraction of the model's largest extent (about UniRig's sampled mesh)
MAX_INFLUENCES = 4  # what glTF carries per vertex
MAX_PASSES = 400  # upper bound on averaging passes


def passes_for(edge: float, radius: float) -> int:
    """Passes that spread a weight over radius. Each pass spreads it about 0.93 edges (the square root of 0.86)."""
    if edge <= 0:
        return 1
    passes = int(np.ceil((radius / edge) ** 2 / 0.86))
    return min(max(passes, 1), MAX_PASSES)


def smooth_weights(points: np.ndarray, faces: np.ndarray, weights: np.ndarray, radius: float) -> np.ndarray:
    _, inverse = np.unique(points.round(5), axis=0, return_inverse=True)
    inverse = inverse.ravel()
    welded_count = int(inverse.max()) + 1
    welded_points = np.zeros((welded_count, 3))
    welded_points[inverse] = points
    welded = np.zeros((welded_count, weights.shape[1]), np.float32)
    np.add.at(welded, inverse, weights)
    welded /= np.bincount(inverse, minlength=welded_count)[:, None]

    corners = inverse[faces]
    edges = np.concatenate([corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    edges = edges[edges[:, 0] != edges[:, 1]]
    if len(edges) == 0:
        return weights.copy()

    lengths = np.linalg.norm(welded_points[edges[:, 0]] - welded_points[edges[:, 1]], axis=1)
    passes = passes_for(float(np.median(lengths)), radius)
    adjacency = sparse.coo_matrix(
        (np.ones(len(edges) * 2, np.float32), (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(welded_count, welded_count),
    ).tocsr()
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    for _ in range(passes):
        welded = (welded + adjacency @ welded) / (degree[:, None] + 1)

    keep = np.argsort(-welded, axis=1)[:, :MAX_INFLUENCES]
    limited = np.zeros_like(welded)
    rows = np.arange(welded_count)[:, None]
    limited[rows, keep] = welded[rows, keep]
    limited /= np.maximum(limited.sum(axis=1, keepdims=True), 1e-9)
    return limited[inverse]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: smooth_skin.py SOURCE.glb TARGET.glb", file=sys.stderr)
        return 2
    import bpy
    import mathutils

    source, target = Path(argv[1]), Path(argv[2])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "FINISHED" not in bpy.ops.import_scene.gltf(filepath=str(source)):
        print(f"could not import {source}", file=sys.stderr)
        return 1
    meshes = [
        o
        for o in bpy.data.objects
        if o.type == "MESH"
        and not any(c.name == "glTF_not_exported" for c in o.users_collection)
        and len(o.vertex_groups) > 0
    ]
    if not meshes:
        print(f"no skinned mesh in {source}", file=sys.stderr)
        return 1

    corners = np.array([tuple(obj.matrix_world @ mathutils.Vector(corner)) for obj in meshes for corner in obj.bound_box])
    radius = SPREAD * float((corners.max(axis=0) - corners.min(axis=0)).max())
    for obj in meshes:
        mesh = obj.data
        count = len(mesh.vertices)
        weights = np.zeros((count, len(obj.vertex_groups)), np.float32)
        for vertex in mesh.vertices:
            for element in vertex.groups:
                weights[vertex.index, element.group] = element.weight
        points = np.zeros(count * 3)
        mesh.vertices.foreach_get("co", points)
        polygons = np.zeros(len(mesh.polygons) * 3, np.int64)
        mesh.polygons.foreach_get("vertices", polygons)
        result = smooth_weights(points.reshape(-1, 3), polygons.reshape(-1, 3), weights, radius)

        everything = list(range(count))
        for group in obj.vertex_groups:
            group.remove(everything)
        by_group = list(obj.vertex_groups)
        rows, columns = np.nonzero(result > 1e-4)
        for row, column in zip(rows.tolist(), columns.tolist()):
            by_group[column].add([row], float(result[row, column]), "REPLACE")
        print("SMOOTHED", obj.name, count, flush=True)

    partial = target.with_name(target.stem + ".partial.glb")
    try:
        if "FINISHED" not in bpy.ops.export_scene.gltf(filepath=str(partial), export_format="GLB"):
            print(f"could not export {target}", file=sys.stderr)
            return 1
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()
    return 0


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    # bpy 4.2 segfaults while the interpreter shuts down after a glTF import and export.
    os._exit(code)
