"""Give a finished, rigged app job a sharper texture: each skeleton part retextured from its close-up.

    python scripts/kit_assembly/texture_pass.py JOB [--home data] [--cards 0,1] [--engine URL]

JOB is a job id or a folder in data/jobs whose `model.glb` has a skeleton. The run folder is
data/texture_runs/<job_id>/: the job's model and input image are copied there, its skeleton is
read (rig_read.py), the input image is cut into one crop per part (crops.py), and every part is
retextured by TRELLIS.2 from its crop on the model's own UVs, the cards working at once
(retexture.py). Only the model's base color texture changes; its mesh and skeleton stay.

The last line is the result the engine reads (same tag as the head pass, run.py):
`MODEL3D_HEAD_PASS {"model": PATH, "applied": BOOL, "reason": TEXT}`. `--engine` is accepted for
the engine's runner and not used: this pass does not generate through the engine.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from run import StepFailed, report, step  # noqa: E402

STEP_TAG = "[run]  "


def visible_cards() -> list[str]:
    """CUDA card indexes in PCI bus order, from nvidia-smi; ["0"] when it cannot tell."""
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ["0"]
    return [line.strip() for line in output.splitlines() if line.strip()] or ["0"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job", help="job id or folder in data/jobs")
    parser.add_argument("--home", type=Path, default=ROOT / "data", help="data folder with jobs/ (MODEL3D_HOME)")
    parser.add_argument("--cards", help="comma-separated CUDA card indexes; every card by default")
    parser.add_argument("--engine", help="unused; accepted for the engine's runner")
    args = parser.parse_args(argv)
    job = Path(args.job) if Path(args.job).is_dir() else args.home / "jobs" / args.job
    model = job / "model.glb"
    image = job / "trellis_input.png"
    if not model.exists() or not image.exists():
        print(f"[fail] {job} has no model.glb or trellis_input.png", file=sys.stderr)
        return 1
    run = args.home / "texture_runs" / job.name
    run.mkdir(parents=True, exist_ok=True)
    log = run / "run.log"
    cards = args.cards.split(",") if args.cards else visible_cards()
    try:
        for name, source in (("input.png", image), ("mold.glb", model), ("mold_rig.glb", model)):
            shutil.copy(source, run / name)
        tool = lambda name: str(TOOLS / f"{name}.py")
        step("read the skeleton", [tool("rig_read"), str(run / "mold_rig.glb"), str(run)], run / "rig.json", log)
        rig = json.loads((run / "rig.json").read_text())
        if rig["reason"] is not None:
            report(model, False, rig["reason"])
            return 0
        step("crop the parts", [tool("crops"), str(run)], run / "crops.json", log)
        from retexture import retexture

        print(f"{STEP_TAG}paint each part ({len(cards)} card{'s' if len(cards) > 1 else ''} at once)", flush=True)
        result = retexture(run, run / "model.glb", cards, 1024,
                           on_region=lambda name, done, total: print(f"{STEP_TAG}painted {name} ({done} of {total})", flush=True))
        (run / "texture.json").write_text(json.dumps(result, indent=2))
    except StepFailed as error:
        print(f"[fail] {error}", file=sys.stderr)
        return 1
    if "error" in result:
        report(model, False, f"the texture pass failed: {result['error'].splitlines()[0]}")
        return 0
    report(run / "model.glb", True, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
