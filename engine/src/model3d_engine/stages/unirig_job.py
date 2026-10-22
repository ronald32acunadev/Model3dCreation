"""Rig one GLB with UniRig. Runs with UniRig's own interpreter, from UniRig's folder.

Usage: python unirig_job.py --unirig-dir DIR --input GLB --output GLB --workdir DIR --max-faces N

It follows UniRig's launch/inference scripts (generate_skeleton.sh, generate_skin.sh, merge.sh)
with their defaults, but calls their Python modules directly: extract.sh runs `pip install` on
every call. Protocol on stdout: `PROGRESS <0..1> <message>` lines, then `RESULT <vertices> <faces>`;
on failure `ERROR <code> <message>` and a non-zero exit. UniRig's own output goes to stderr.
Only the standard library is imported at module level, so the engine's tests can import it.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

SUFFIXES = "obj,fbx,FBX,dae,glb,gltf,vrm"
SKELETON_TASK = "configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml"
SKIN_TASK = "configs/task/quick_inference_unirig_skin.yaml"
SEED = 12345
INFERENCE_FACES = 50000  # what UniRig samples its point cloud from, not the output mesh
# torch>=2.6 loads checkpoints with weights_only=True, and UniRig's skin checkpoint pickles a
# box.Box; allow that one class instead of turning the check off.
RUN_BOOTSTRAP = (
    "import runpy, sys, box, torch; "
    "torch.serialization.add_safe_globals([box.Box]); "
    "sys.argv = ['run.py'] + sys.argv[1:]; "
    "runpy.run_path('run.py', run_name='__main__')"
)
# (progress after the step, message) for each step name.
STEP_PROGRESS = {
    "extract": (0.1, "Mesh prepared"),
    "skeleton": (0.45, "Skeleton ready"),
    "extract skin": (0.5, "Skeleton prepared"),
    "skin": (0.85, "Skin weights ready"),
    "merge": (1.0, "Skeleton added"),
}


class JobError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def classify_failure(stderr: str) -> str:
    """The engine error code for a failed UniRig step, from its stderr."""
    if "OutOfMemoryError" in stderr or "CUDA out of memory" in stderr:
        return "gpu_out_of_memory"
    return "rig_failed"


def step_commands(python: str, mesh: Path, output: Path, work: Path, stamp: str) -> list[tuple[str, list[str], Path]]:
    """(name, command, file it must produce) for each UniRig step, in order."""
    npz = work / "npz"
    skeleton, skin = work / "skeleton.fbx", work / "skin.fbx"

    def extract(source: Path) -> list[str]:
        return [
            python, "-m", "src.data.extract",
            "--config=configs/data/quick_inference.yaml",
            f"--require_suffix={SUFFIXES}",
            "--force_override=true",
            "--num_runs=1",
            "--id=0",
            f"--time={stamp}",
            f"--faces_target_count={INFERENCE_FACES}",
            f"--input={source}",
            f"--output_dir={npz}",
        ]

    def run(task: str, source: Path, target: Path, *extra: str) -> list[str]:
        return [python, "-c", RUN_BOOTSTRAP, f"--task={task}", f"--seed={SEED}", f"--input={source}",
                f"--output={target}", f"--npz_dir={npz}", *extra]

    return [
        ("extract", extract(mesh), npz),
        ("skeleton", run(SKELETON_TASK, mesh, skeleton), skeleton),
        ("extract skin", extract(skeleton), npz),
        ("skin", run(SKIN_TASK, skeleton, skin, "--data_name=raw_data.npz"), skin),
        ("merge", [python, "-m", "src.inference.merge", f"--require_suffix={SUFFIXES}", "--num_runs=1", "--id=0",
                   f"--source={skin}", f"--target={mesh}", f"--output={output}"], output),
    ]


def _is_textured(scene) -> bool:
    for geometry in scene.geometry.values():
        material = getattr(geometry.visual, "material", None)
        if material is not None and (
            getattr(material, "image", None) is not None or getattr(material, "baseColorTexture", None) is not None
        ):
            return True
    return False


def prepare_mesh(source: Path, work: Path, max_faces: int) -> Path:
    """The mesh to rig: the input, or a decimated copy when it is untextured and too dense."""
    import trimesh

    scene = trimesh.load(source, force="scene")
    faces = sum(len(geometry.faces) for geometry in scene.geometry.values())
    if faces <= max_faces or _is_textured(scene):
        return source  # textured models were already capped by the paint stage
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
    target = work / "decimated.glb"
    mesh.export(target)
    return target


def count(path: Path) -> tuple[int, int]:
    import trimesh

    scene = trimesh.load(path, force="scene")
    return (
        sum(len(geometry.vertices) for geometry in scene.geometry.values()),
        sum(len(geometry.faces) for geometry in scene.geometry.values()),
    )


def rig(unirig_dir: Path, source: Path, output: Path, work: Path, max_faces: int) -> tuple[int, int]:
    work.mkdir(parents=True, exist_ok=True)
    mesh = prepare_mesh(source, work, max_faces)
    stamp = time.strftime("%Y_%m_%d_%H_%M_%S")
    for name, command, produced in step_commands(sys.executable, mesh, output, work, stamp):
        done = subprocess.run(command, cwd=unirig_dir, capture_output=True, text=True)
        sys.stderr.write(done.stdout + done.stderr)
        sys.stderr.flush()
        # UniRig's scripts can exit 0 after a failure, so the produced file is the real check.
        if done.returncode != 0 or not produced.exists():
            tail = [line for line in done.stderr.splitlines() if line.strip()][-1:] or ["no output"]
            raise JobError(classify_failure(done.stderr), f"UniRig {name} step failed: {tail[0]}")
        value, message = STEP_PROGRESS[name]
        print(f"PROGRESS {value} {message}", flush=True)
    return count(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unirig-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--max-faces", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        vertices, faces = rig(args.unirig_dir, args.input, args.output, args.workdir, args.max_faces)
    except JobError as error:
        print(f"ERROR {error.code} {error.message}", flush=True)
        return 1
    except Exception as error:  # report anything else in the protocol, not as a bare traceback
        print(f"ERROR rig_failed {type(error).__name__}: {error}", flush=True)
        return 1
    print(f"RESULT {vertices} {faces}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
