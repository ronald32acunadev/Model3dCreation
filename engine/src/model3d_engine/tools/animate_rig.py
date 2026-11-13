"""Detects the body type of a rigged GLB and adds a procedural walk cycle to it.

Runs with the engine's Python, which ships bpy:
`python animate_rig.py detect SOURCE.glb` or `python animate_rig.py walk SOURCE.glb TARGET.glb BODY`.
This file must not import model3d_client; the client starts it as a separate process.

Bone names are never used. Legs and arms come from rest-pose positions and the bone hierarchy only.
bpy prints its own log lines, so the client reads the single line tagged with RESULT_TAG.
"""

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

RESULT_TAG = "MODEL3D_RESULT "
BODIES = ("biped", "quadruped")
USAGE = (
    "Usage: animate_rig.py detect SOURCE.glb\n"
    "       animate_rig.py walk SOURCE.glb TARGET.glb biped|quadruped"
)

FRAMES = 24
KEY_STEP = 2
THIGH_DEG = 25.0
KNEE_DEG = 35.0
ARM_DEG = 15.0
BOB_FACTOR = 0.02  # fraction of the model height
LEG_TIP_FRACTION = 0.15  # a leg tip lies within the lowest 15 % of the height
ARM_START_FRACTION = 0.5  # an arm starts above half the height
ARM_REACH_FRACTION = 0.15  # an arm moves laterally by more than 15 % of the height

# Phase offsets for a quadruped whose legs cannot be split into front/back and left/right.
DIAGONAL_OFFSETS = (0.0, math.pi, math.pi, 0.0)


@dataclass
class Rig:
    armature: object
    matrix: object  # armature object's world matrix
    root: object  # first bone without parent, or None
    up: int  # axis index of the model's tallest extent
    ground: float  # lowest mesh coordinate on the up axis
    height: float  # mesh extent on the up axis
    legs: list  # chains (lists of Bone), root to tip
    arms: list
    forward: object  # horizontal unit vector the feet point to
    lateral: object  # up cross forward


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("detect", "walk"):
        print(USAGE, file=sys.stderr)
        return 2
    if argv[1] == "detect" and len(argv) != 3:
        print(USAGE, file=sys.stderr)
        return 2
    if argv[1] == "walk" and (len(argv) != 5 or argv[4] not in BODIES):
        print(USAGE, file=sys.stderr)
        return 2

    import bpy

    source = Path(argv[2])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if "FINISHED" not in bpy.ops.import_scene.gltf(filepath=str(source)):
        print(f"Could not import {source}", file=sys.stderr)
        return 1

    armature = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if armature is None:
        print("The model has no skeleton", file=sys.stderr)
        return 1
    rig = analyze(bpy, armature)
    if rig is None:
        print("The model has no mesh", file=sys.stderr)
        return 1

    if argv[1] == "detect":
        print(RESULT_TAG + json.dumps(detection_payload(rig), separators=(",", ":")))
        return 0
    return walk(bpy, rig, Path(argv[3]), argv[4])


def analyze(bpy, armature) -> Rig | None:
    from mathutils import Vector

    corners = [o.matrix_world @ Vector(c) for o in model_meshes(bpy) for c in o.bound_box]
    if not corners:
        return None
    lows = [min(v[i] for v in corners) for i in range(3)]
    highs = [max(v[i] for v in corners) for i in range(3)]
    extents = [highs[i] - lows[i] for i in range(3)]
    up = max(range(3), key=lambda i: extents[i])

    matrix = armature.matrix_world
    bones = list(armature.data.bones)
    legs, arms = classify(find_chains(bones), up, lows[up], extents[up], matrix)
    forward, lateral = frame_axes(legs, up, matrix)
    root = next((b for b in bones if b.parent is None), None)
    return Rig(armature, matrix, root, up, lows[up], extents[up], legs, arms, forward, lateral)


def model_meshes(bpy) -> list:
    """Mesh objects that belong to the model. The glTF importer parks its placeholder meshes
    (for example an Icosphere) in a 'glTF_not_exported' collection; they are not part of the model."""
    return [
        o for o in bpy.data.objects
        if o.type == "MESH" and not any(c.name == "glTF_not_exported" for c in o.users_collection)
    ]


def find_chains(bones) -> list[list]:
    """Splits the hierarchy into chains. Each chain starts at a root child or at a child of a
    branching bone (2+ children), follows single children, and ends at a leaf or a branching bone."""
    starts = []
    for bone in bones:
        if bone.parent is None or len(bone.children) >= 2:
            starts.extend(bone.children)
    chains = []
    for start in starts:
        chain = [start]
        while len(chain[-1].children) == 1:
            chain.append(chain[-1].children[0])
        chains.append(chain)
    return chains


def classify(chains, up, ground, height, matrix):
    legs, arms = [], []
    for chain in chains:
        head = matrix @ chain[0].head_local
        tail = matrix @ chain[-1].tail_local
        if tail[up] <= ground + LEG_TIP_FRACTION * height and head[up] > tail[up]:
            legs.append(chain)
            continue
        if head[up] > ground + ARM_START_FRACTION * height:
            displacement = tail - head
            displacement[up] = 0.0
            if displacement.length > ARM_REACH_FRACTION * height:
                arms.append(chain)
    return legs, arms


def frame_axes(legs, up, matrix):
    from mathutils import Vector

    horizontal = Vector((0.0, 0.0, 0.0))
    for chain in legs:
        last = chain[-1]
        direction = (matrix @ last.tail_local) - (matrix @ last.head_local)
        direction[up] = 0.0
        horizontal += direction
    if horizontal.length < 1e-6:
        # No usable feet direction: default to -Y, or -Z when -Y is the up axis.
        horizontal = Vector((0.0, -1.0, 0.0)) if up != 1 else Vector((0.0, 0.0, -1.0))
    forward = horizontal.normalized()
    up_vector = Vector((0.0, 0.0, 0.0))
    up_vector[up] = 1.0
    lateral = up_vector.cross(forward).normalized()
    return forward, lateral


def detection_payload(rig) -> dict:
    body = "biped" if len(rig.legs) == 2 else "quadruped" if len(rig.legs) == 4 else "unknown"
    return {
        "body": body,
        "legs": [[bone.name for bone in chain] for chain in rig.legs],
        "arms": [[bone.name for bone in chain] for chain in rig.arms],
        "up": rig.up,
    }


def walk(bpy, rig, target: Path, body: str) -> int:
    from mathutils import Vector

    if not rig.legs:
        print("No legs found to animate", file=sys.stderr)
        return 1

    root_position = rig.matrix @ rig.root.head_local if rig.root is not None else Vector((0.0, 0.0, 0.0))
    legs_plan, arms_plan = plan_walk(rig, body, root_position)
    pose_bones = rig.armature.pose.bones

    rig.armature.animation_data_create()
    action = bpy.data.actions.new("Walk")
    rig.armature.animation_data.action = action
    scene = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = FRAMES

    for frame in range(0, FRAMES + 1, KEY_STEP):
        phase = 2.0 * math.pi * frame / FRAMES
        for chain, offset in legs_plan:
            rotate(rig, pose_bones[chain[0].name], rig.lateral, math.radians(THIGH_DEG) * math.sin(phase + offset), frame)
            if len(chain) > 1:
                # Knee rule: rotating the shin by a positive angle moves its tip along
                # lateral x rest_direction. The sign flips the axis so that tip moves toward -forward.
                knee = chain[1]
                direction = (rig.matrix @ knee.tail_local) - (rig.matrix @ knee.head_local)
                sign = 1.0 if rig.lateral.cross(direction).dot(-rig.forward) >= 0.0 else -1.0
                bend = math.radians(KNEE_DEG) * max(0.0, math.sin(phase + offset + math.pi / 2))
                rotate(rig, pose_bones[knee.name], rig.lateral * sign, bend, frame)
        for chain, offset in arms_plan:
            swing = math.radians(ARM_DEG) * math.sin(phase + offset + math.pi)
            rotate(rig, pose_bones[chain[0].name], rig.lateral, swing, frame)
        if rig.root is not None:
            root_pose = pose_bones[rig.root.name]
            up_vector = Vector((0.0, 0.0, 0.0))
            up_vector[rig.up] = 1.0
            lift = BOB_FACTOR * rig.height * abs(math.sin(phase))
            root_pose.location = (rig.matrix @ rig.root.matrix_local).to_3x3().inverted() @ (up_vector * lift)
            root_pose.keyframe_insert("location", frame=frame)

    target.parent.mkdir(parents=True, exist_ok=True)
    if "FINISHED" not in bpy.ops.export_scene.gltf(filepath=str(target), export_format="GLB", export_animations=True):
        print(f"Could not write {target}", file=sys.stderr)
        return 1
    payload = {"body": body, "legs": len(rig.legs), "arms": len(rig.arms), "frames": FRAMES}
    print(RESULT_TAG + json.dumps(payload, separators=(",", ":")))
    return 0


def plan_walk(rig, body: str, root_position):
    """Returns ([(leg chain, phase offset)], [(arm chain, phase offset)]).

    biped: the first two legs, ordered by lateral side, get phases 0 and pi.
    quadruped: with four legs, the two largest forward coordinates are the front pair. Within each
    pair, the left leg (higher lateral coordinate) and the diagonal opposite leg share a phase: front-left
    and back-right get 0, front-right and back-left get pi. Otherwise the DIAGONAL_OFFSETS rule by index.
    Arms are used for biped only; each arm takes the phase of the nearest leg, shifted by pi.
    """
    def lateral_of(chain):
        return (rig.matrix @ chain[0].head_local - root_position).dot(rig.lateral)

    def forward_of(chain):
        return (rig.matrix @ chain[0].head_local).dot(rig.forward)

    if body == "biped":
        legs = sorted(rig.legs[:2], key=lateral_of)
        legs_plan = [(chain, offset) for chain, offset in zip(legs, (0.0, math.pi))]
        arms_plan = []
        for arm in rig.arms:
            nearest = min(range(len(legs_plan)), key=lambda i: abs(lateral_of(arm) - lateral_of(legs_plan[i][0])))
            arms_plan.append((arm, legs_plan[nearest][1]))
        return legs_plan, arms_plan

    legs = rig.legs[:4]
    if len(legs) == 4:
        ranked = sorted(legs, key=forward_of, reverse=True)
        front = sorted(ranked[:2], key=lateral_of, reverse=True)
        back = sorted(ranked[2:], key=lateral_of, reverse=True)
        plan = [(front[0], 0.0), (front[1], math.pi), (back[0], math.pi), (back[1], 0.0)]
    else:
        plan = [(chain, DIAGONAL_OFFSETS[i % 4]) for i, chain in enumerate(legs)]
    return plan, []


def rotate(rig, pose_bone, world_axis, angle: float, frame: int) -> None:
    """Sets the pose bone's rotation to `angle` about a world axis, keyed at `frame`.

    The axis is converted into the bone's local space, since quaternions are applied there."""
    from mathutils import Quaternion

    local_axis = ((rig.matrix @ pose_bone.bone.matrix_local).to_3x3().inverted() @ world_axis).normalized()
    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.rotation_quaternion = Quaternion(local_axis, angle)
    pose_bone.keyframe_insert("rotation_quaternion", frame=frame)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
