"""Read a rigged GLB into kit parts: joints, bone segments, and a part label per vertex.

Run with the engine's interpreter, which ships bpy:
`python rig_read.py RIGGED.glb OUT_DIR` writes OUT_DIR/rig.json and OUT_DIR/rig_labels.npz.
Bone names are never used; parts come from rest positions and the hierarchy. Coordinates are
written in the glTF frame (+Y up). See README.md for the file formats.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LEG_TIP_FRACTION = 0.15  # a leg's tip lies within the lowest 15 % of the height
HEAD_START_FRACTION = 0.6  # the head chain starts above 60 % of the height
CLAVICLE_SLOPE = 1.0  # a first arm bone flatter than 45 degrees is a clavicle and stays in the torso


@dataclass
class Bone:
    name: str
    head: np.ndarray
    tail: np.ndarray
    parent: str | None
    children: list[str] = field(default_factory=list)


def to_gltf(vector) -> np.ndarray:
    """Blender world (x, y, z) to the glTF frame (x, z, -y)."""
    return np.array([vector[0], vector[2], -vector[1]], np.float64)


def find_chains(bones: dict[str, Bone]) -> list[list[str]]:
    """Chains start at a root child or a branching bone's child and follow single children."""
    starts = [child for bone in bones.values() if bone.parent is None or len(bone.children) >= 2 for child in bone.children]
    chains = []
    for start in starts:
        chain = [start]
        while len(bones[chain[-1]].children) == 1:
            chain.append(bones[chain[-1]].children[0])
        chains.append(chain)
    return chains


def subtree(bones: dict[str, Bone], name: str) -> list[str]:
    """A bone and all its descendants."""
    names = [name]
    for child in bones[name].children:
        names.extend(subtree(bones, child))
    return names


def side(bones: dict[str, Bone], names: list[str]) -> str:
    """'px' or 'nx' by the sign of the bones' mean tip x."""
    return "px" if np.mean([bones[n].tail[0] for n in names]) >= 0 else "nx"


def classify(bones: dict[str, Bone], ground: float, height: float) -> dict[str, list[str]]:
    """Map part names to the bones they own. Bones in no part belong to the torso."""
    chains = find_chains(bones)
    parts: dict[str, list[str]] = {}
    legs = [
        c for c in chains
        if bones[c[-1]].tail[1] <= ground + LEG_TIP_FRACTION * height and bones[c[0]].head[1] > bones[c[-1]].tail[1]
    ]
    for chain in legs:
        parts[f"leg_{side(bones, chain)}"] = subtree(bones, chain[0])

    rising = [
        c for c in chains
        if c not in legs
        and bones[c[0]].head[1] > ground + HEAD_START_FRACTION * height
        and bones[c[-1]].tail[1] - bones[c[0]].head[1] > abs(bones[c[-1]].tail[0] - bones[c[0]].head[0])
    ]
    if rising:
        head = max(rising, key=lambda c: bones[c[-1]].tail[1])
        parts["head"] = subtree(bones, head[0])
        branch = bones[head[0]].parent
        siblings = [c for c in chains if c is not head and c not in legs and bones[c[0]].parent == branch]
        for chain in siblings:
            names = subtree(bones, chain[0])
            first = bones[chain[0]]
            drop = first.head[1] - first.tail[1]
            run = np.linalg.norm((first.tail - first.head)[[0, 2]])
            if len(chain) > 1 and drop < CLAVICLE_SLOPE * run:
                names = subtree(bones, chain[1])
            parts[f"arm_{side(bones, names)}"] = names
    return parts


def joint(bones: dict[str, Bone], names: list[str]) -> dict:
    """Where a part meets the torso, the direction into the part, and its bone segments."""
    first = bones[names[0]]
    direction = first.tail - first.head
    return {
        "point": first.head.tolist(),
        "normal": (direction / np.linalg.norm(direction)).tolist(),
        "segments": [[bones[n].head.tolist(), bones[n].tail.tolist()] for n in names],
    }


def read(path: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    """Import the GLB and return (rig.json content, vertex positions, vertex labels)."""
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "FINISHED" not in bpy.ops.import_scene.gltf(filepath=str(path)):
        raise SystemExit(f"Could not import {path}")
    meshes = [
        o for o in bpy.data.objects
        if o.type == "MESH" and not any(c.name == "glTF_not_exported" for c in o.users_collection)
    ]
    positions = np.array([to_gltf(o.matrix_world @ v.co) for o in meshes for v in o.data.vertices])
    armature = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if armature is None:
        return {"parts": ["torso"], "joints": {}, "missing": [], "reason": "The model has no skeleton"}, positions, np.zeros(len(positions), np.int8)

    matrix = armature.matrix_world
    bones = {
        b.name: Bone(b.name, to_gltf(matrix @ b.head_local), to_gltf(matrix @ b.tail_local), b.parent.name if b.parent else None)
        for b in armature.data.bones
    }
    for bone in bones.values():
        if bone.parent is not None:
            bones[bone.parent].children.append(bone.name)
    ground, top = positions[:, 1].min(), positions[:, 1].max()
    owned = classify(bones, ground, top - ground)
    expected = ["head", "arm_px", "arm_nx", "leg_px", "leg_nx"]
    names = ["torso"] + [p for p in expected if p in owned] + sorted(p for p in owned if p not in expected)
    in_parts = {bone for owned_bones in owned.values() for bone in owned_bones}
    rig = {
        "parts": names,
        "joints": {p: joint(bones, owned[p]) for p in names[1:]},
        "torso_segments": [[b.head.tolist(), b.tail.tolist()] for b in bones.values() if b.name not in in_parts],
        "missing": [p for p in expected if p not in owned],
        "reason": None if owned else "No head, arm, or leg was found in the skeleton",
    }

    part_of_bone = {bone: names.index(p) for p, owned_bones in owned.items() for bone in owned_bones}
    weights = np.zeros((len(positions), len(names)))
    offset = 0
    for o in meshes:
        groups = {g.index: g.name for g in o.vertex_groups}
        for v in o.data.vertices:
            for g in v.groups:
                weights[offset + v.index, part_of_bone.get(groups.get(g.group), 0)] += g.weight
        offset += len(o.data.vertices)
    return rig, positions, np.argmax(weights, axis=1).astype(np.int8)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: rig_read.py RIGGED.glb OUT_DIR", file=sys.stderr)
        return 2
    out = Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rig, positions, labels = read(Path(argv[1]))
    (out / "rig.json").write_text(json.dumps(rig, indent=2))
    np.savez(out / "rig_labels.npz", positions=positions, labels=labels)
    counts = {name: int((labels == i).sum()) for i, name in enumerate(rig["parts"])}
    print(json.dumps({"parts": counts, "missing": rig["missing"], "reason": rig["reason"]}))
    return 0


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    # bpy can hang in interpreter teardown after the work is written; exit without it.
    os._exit(code)
