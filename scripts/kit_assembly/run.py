"""Give a finished app job a sculpted head in one command (the head pass of the kit experiment).

    python scripts/kit_assembly/run.py JOB [--extra-seeds 11] [--engine URL]

JOB is a job id or a folder in data/jobs; a rigged job (`rig: true`) gets the full pass, any other
ends with its own model and the reason. The run folder is data/kit_runs/<job_id>/. Steps whose
outputs exist are skipped, so a rerun resumes. When no engine answers, one is started with
TRELLIS.2 for the generation and stopped afterwards; a running engine is used and left running.
Run it with any Python 3.11+; every step runs in the engine's interpreter.
"""

import argparse
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
PYTHON = ROOT / "engine/.venv/bin/python"
MEMORY_LIMIT = 40 * 2**30  # an earlier exploration script used 57 GB and was killed by the kernel
STEP_TIMEOUT_S = 3600
ENGINE_START_TIMEOUT_S = 180


class StepFailed(Exception):
    pass


def limit_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))


def step(name: str, args: list[str], done: Path | None, log: Path) -> None:
    """Run one tool in the engine's interpreter, unless its output `done` already exists."""
    if done is not None and done.exists():
        print(f"[skip] {name}: {done.name} exists", flush=True)
        return
    print(f"[run]  {name}", flush=True)
    started = time.monotonic()
    with open(log, "a") as out:
        out.write(f"\n===== {name}: {' '.join(args)}\n")
        out.flush()
        try:
            result = subprocess.run([str(PYTHON), *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                    preexec_fn=limit_memory, timeout=STEP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise StepFailed(f"{name} did not finish within {STEP_TIMEOUT_S} s (log: {log})") from None
    if result.returncode != 0 or (done is not None and not done.exists()):
        tail = "".join(log.read_text().splitlines(keepends=True)[-15:])
        raise StepFailed(f"{name} failed with exit code {result.returncode}:\n{tail}")
    print(f"       {name} took {time.monotonic() - started:.0f} s", flush=True)


def engine_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/v1/health", timeout=3) as response:
            return bool(json.loads(response.read()).get("ready"))
    except OSError:
        return False


def engine_mode(url: str) -> str:
    """The engine's run-mode reason, which names TRELLIS.2 when it generates with it."""
    with urllib.request.urlopen(f"{url}/v1/health", timeout=3) as response:
        return json.loads(response.read())["mode"].get("reason") or ""


def start_engine(url: str, log: Path) -> subprocess.Popen:
    """Start the engine with TRELLIS.2 in its own process group and wait until it is ready."""
    env = {**os.environ, "MODEL3D_GENERATOR": "trellis2"}
    with open(log, "a") as out:
        process = subprocess.Popen([str(PYTHON), "-m", "model3d_engine"], cwd=ROOT / "engine", env=env,
                                   stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + ENGINE_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if engine_ready(url):
            return process
        if process.poll() is not None:
            raise StepFailed(f"the engine exited with code {process.returncode} (log: {log})")
        time.sleep(2)
    stop_engine(process)
    raise StepFailed(f"the engine was not ready within {ENGINE_START_TIMEOUT_S} s (log: {log})")


def stop_engine(process: subprocess.Popen) -> None:
    """Stop the engine and its TRELLIS.2 worker (same process group)."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        pass


def prepare(job: Path, run: Path) -> None:
    """Copy the job's input image, mold, rigged model, and record into the run folder."""
    run.mkdir(parents=True, exist_ok=True)
    rigged = (job / "rig_input.glb").exists()
    copies = {
        "input.png": job / "trellis_input.png",
        "mold.glb": job / ("rig_input.glb" if rigged else "model.glb"),
        "mold_rig.glb": job / "model.glb",
        "job.json": job / "job.json",
    }
    for name, source in copies.items():
        if not source.exists():
            raise StepFailed(f"the job has no {source.name}; only finished jobs can be used")
        if not (run / name).exists():
            shutil.copy(source, run / name)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job", help="job id or folder in data/jobs, or 'latest' for the newest rigged job")
    parser.add_argument("--extra-seeds", default="11", help="comma-separated seeds tried besides the job's (empty for none)")
    parser.add_argument("--engine", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)
    rigged = sorted((p for p in (ROOT / "data/jobs").glob("*") if (p / "rig_input.glb").exists()),
                    key=lambda p: (p / "job.json").stat().st_mtime, reverse=True)
    if args.job == "latest" and rigged:
        job = rigged[0]
    else:
        job = Path(args.job) if Path(args.job).is_dir() else ROOT / "data/jobs" / args.job
    if not job.is_dir():
        print(f"No job folder {job}. Pass a job id from data/jobs, or 'latest'. Rigged jobs, newest first:", file=sys.stderr)
        for path in rigged[:10]:
            print(f"  {path.name}", file=sys.stderr)
        return 2
    print(f"Job {job.name}", flush=True)
    run = ROOT / "data/kit_runs" / job.name
    log = run / "run.log"
    started = time.monotonic()
    try:
        prepare(job, run)
        tool = lambda name: str(TOOLS / f"{name}.py")
        step("read the skeleton", [tool("rig_read"), str(run / "mold_rig.glb"), str(run)], run / "rig.json", log)
        rig = json.loads((run / "rig.json").read_text())
        if rig["reason"] is None:
            step("crop the parts", [tool("crops"), str(run)], run / "crops.json", log)
            part = "head_bust" if "head_bust" in json.loads((run / "crops.json").read_text())["parts"] else "head"
            seeds = [str(json.loads((run / "job.json").read_text())["seed"])]
            seeds += [s for s in args.extra_seeds.split(",") if s.strip()]
            wanted = [run / "parts" / (f"{part}.glb" if i == 0 else f"{part}@{s}.glb") for i, s in enumerate(seeds)]
            record = json.loads((run / "parts.json").read_text()) if (run / "parts.json").exists() else {}
            failed = {key for key, job in record.items() if job.get("state") == "failed"}
            if not all(p.exists() or p.stem in failed for p in wanted):
                engine = None if engine_ready(args.engine) else start_engine(args.engine, log)
                if "TRELLIS.2" not in engine_mode(args.engine):
                    raise StepFailed("the running engine does not use TRELLIS.2; stop it (or the app) and run again")
                try:
                    step("sculpt the head", [tool("generate_parts"), str(run), "--parts", part, "--seeds", ",".join(seeds),
                                             "--engine", args.engine], None, log)
                finally:
                    if engine is not None:
                        stop_engine(engine)
            step("align the head", [tool("align"), str(run)], run / "align.json", log)
        step("cut the head and blend the neck", [tool("kit"), str(run), "--head-swap"], run / "kit.json", log)
        step("paint each piece", [tool("paint"), str(run)], run / "paint.json", log)
        if rig["reason"] is None:
            step("render the comparison", [tool("compare"), str(run)], run / "compare.json", log)
    except StepFailed as error:
        print(f"[fail] {error}", file=sys.stderr)
        return 1

    kit = json.loads((run / "kit.json").read_text())
    print(f"\nDone in {time.monotonic() - started:.0f} s. Run folder: {run}")
    if "skipped" in kit:
        print(f"No head pass: {kit['skipped']}. model.glb is the job's model.")
    else:
        head = kit["volume_check"].get("head", {})
        print(f"Head: {head.get('used')} ({head.get('file') or 'mold'}); solid-volume ratios {head.get('ratios')}")
        print(f"Model: {run / 'model.glb'}")
        print(f"Compare: {run / 'compare_head.png'}, {run / 'compare_front.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
