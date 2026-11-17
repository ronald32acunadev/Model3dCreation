"""Rerun the head pass's CPU steps on saved runs and measure every result side by side.

    python scripts/kit_assembly/evaluate.py OUT [--runs data/kit_runs] [--only ID,...] [--workers 3]

Every run folder with a skeleton and a sculpted head (parts/head*.glb) is rerun with the current
code, from its saved inputs, in OUT/<run>: align, kit --head-swap, paint, then a before/after
render of the head. Nothing in the run folders changes. Per run, OUT/<run>/eval.json holds:

- the alignment's method, fitness and two-way mean distances (align.json);
- the new head's nose, chin and throat heights (landmarks.py) and the cut's height in front of
  the chin, relative to the chin and to the head joint (positive: above);
- the share of the head's visible texels whose color came mostly from a bake that missed them
  (they show black), as paint.json reports it;
- the pass's skip reason, when it kept the original model.

OUT/summary.json collects them, OUT/sheet.png shows every before/after pair (red line: the cut;
green: the chin; blue: the throat), and the table is printed as Markdown for the backlog.
Run it with the engine's interpreter, which ships bpy.
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from run import StepFailed, step  # noqa: E402

INPUTS = ("mold.glb", "mold_labels.npy", "rig.json", "crops.json", "input.png", "job.json", "parts")
SIZE = 600


def candidates(runs: Path, only: set[str] | None) -> list[Path]:
    """Run folders the head pass can be rerun on: a skeleton, a bust crop, and sculpted heads."""
    found = []
    for run in sorted(p for p in runs.iterdir() if p.is_dir()):
        if only is not None and run.name not in only:
            continue
        if not all((run / name).exists() for name in INPUTS):
            continue
        if json.loads((run / "rig.json").read_text())["reason"] is not None:
            continue
        if not any((run / "parts").glob("head*.glb")):
            continue
        if "head_bust" not in json.loads((run / "crops.json").read_text())["parts"]:
            continue  # the full-kit experiment, not the head pass
        found.append(run)
    return found


def prepare(source: Path, work: Path) -> None:
    """A fresh workspace that links the run's inputs, so the run folder is never written."""
    work.mkdir(parents=True, exist_ok=True)
    for path in work.iterdir():
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            for inner in sorted(path.rglob("*"), reverse=True):
                inner.unlink() if not inner.is_dir() else inner.rmdir()
            path.rmdir()
    for name in INPUTS:
        (work / name).symlink_to((source / name).resolve())


def rerun(source: Path, work: Path) -> None:
    prepare(source, work)
    log = work / "run.log"
    tool = lambda name: str(TOOLS / f"{name}.py")
    step("align the head", [tool("align"), str(work)], work / "align.json", log)
    step("cut the head and blend the neck", [tool("kit"), str(work), "--head-swap"], work / "kit.json", log)
    step("paint each piece", [tool("paint"), str(work)], work / "paint.json", log)


def measure(work: Path) -> dict:
    from kit import ENCLOSURE, load_mesh, mesh_field
    from landmarks import find_face, plane_height

    rig = json.loads((work / "rig.json").read_text())
    kit = json.loads((work / "kit.json").read_text())
    result: dict = {"skipped": kit.get("skipped")}
    chosen = kit.get("volume_check", {}).get("head", {}).get("file")
    if chosen is None:
        return result
    align = json.loads((work / "align.json").read_text())[Path(chosen).stem]
    result["align"] = {key: align[key] for key in ("method", "fitness", "mean_distance", "reverse_mean_distance")}
    result["head_fit"] = align.get("head")
    result["head_file"] = chosen
    joint = rig["joints"]["head"]["point"]
    bust = load_mesh(work / "aligned" / chosen)
    field = mesh_field(np.asarray(bust.vertices), np.asarray(bust.faces), enclosure=ENCLOSURE)
    lowest = json.loads((work / "crops.json").read_text())["parts"]["head_bust"]["y_cut"]
    try:
        face = find_face(field, lowest, float(bust.bounds[1][1]), joint[1])
    except ValueError as error:
        result["face_error"] = str(error)
        face = None
    if face is not None:
        result["face"] = {"nose_y": face.nose_y, "chin_y": face.chin_y, "throat_y": face.throat_y,
                          "chin_z": face.chin_z, "axis": list(face.axis)}
    plane = kit.get("planes", {}).get("head")
    if plane is not None:
        at = (face.axis[0], face.chin_z) if face is not None else (joint[0], joint[2])
        cut = plane_height(plane, *at)
        result["cut"] = {"y": cut, "above_joint": cut - joint[1],
                         "above_chin": None if face is None else cut - face.chin_y}
    if (work / "model.glb").exists() and not kit.get("skipped"):
        painted = json.loads((work / "paint.json").read_text())["head"]
        result["uncovered_share"] = painted.get("uncovered_texel_share")
        result["unreached_share"] = painted.get("unreached_texel_share")
    return result


def head_view(work: Path) -> tuple[list[float], float]:
    """Center and size of a front view framing the new head from its throat to its top."""
    import trimesh

    kit = json.loads((work / "kit.json").read_text())
    chosen = kit.get("volume_check", {}).get("head", {}).get("file")
    result = json.loads((work / "eval.json").read_text()) if (work / "eval.json").exists() else {}
    if chosen is not None and "face" in result:
        bust = trimesh.load(work / "aligned" / chosen, force="mesh")
        top, bottom = float(bust.bounds[1][1]), result["face"]["throat_y"]
        x, z = result["face"]["axis"]
        return [x, (top + bottom) / 2 - 0.1 * (top - bottom), z], float((top - bottom) * 1.6)
    rig = json.loads((work / "rig.json").read_text())
    mold = trimesh.load(work / "mold.glb", force="mesh")
    labels = np.load(work / "mold_labels.npy")
    head = np.asarray(mold.vertices)[labels == rig["parts"].index("head")]
    lo, hi = head.min(axis=0), head.max(axis=0)
    return ((lo + hi) / 2).tolist(), float(max(hi[:2] - lo[:2]) * 1.15)


def render(work: Path) -> None:
    """Before (the mold) and after (the pass's model) head renders, in this process (bpy)."""
    import bpy

    import compare

    compare.SIZE = SIZE
    center, scale = head_view(work)
    compare.render_model(bpy, work / "mold.glb", {"before": (center, scale)}, work)
    if (work / "model.glb").exists() and not json.loads((work / "kit.json").read_text()).get("skipped"):
        compare.render_model(bpy, work / "model.glb", {"after": (center, scale)}, work)


def annotate(work: Path, result: dict) -> Path:
    """The before/after pair with the cut, chin and throat drawn across both."""
    from PIL import Image, ImageDraw

    center, scale = head_view(work)
    tiles = [Image.open(work / f"{name}.png").convert("RGB") for name in ("before", "after") if (work / f"{name}.png").exists()]
    pair = Image.new("RGB", (SIZE * 2 + 10, SIZE + 60), (40, 40, 40))
    for index, tile in enumerate(tiles):
        pair.paste(tile.resize((SIZE, SIZE)), (index * (SIZE + 10), 60))
    draw = ImageDraw.Draw(pair)
    row = lambda y: 60 + SIZE / 2 - (y - center[1]) / scale * SIZE
    lines = []
    if "cut" in result:
        lines.append((result["cut"]["y"], (230, 40, 40)))
    if "face" in result:
        lines += [(result["face"]["chin_y"], (40, 200, 60)), (result["face"]["throat_y"], (40, 120, 255))]
    for y, color in lines:
        if 60 <= row(y) <= 60 + SIZE:
            draw.line([(0, row(y)), (pair.width, row(y))], fill=color, width=2)
    draw.text((8, 6), work.name, fill=(255, 255, 255))
    draw.text((8, 22), summary_line(result), fill=(255, 255, 255))
    draw.text((8, 38), "before" + " " * 100 + "after", fill=(200, 200, 200))
    path = work / "pair.png"
    pair.save(path)
    return path


def fmt(value, digits: int = 3) -> str:
    return "-" if value is None else f"{value:+.{digits}f}" if isinstance(value, float) else str(value)


def summary_line(result: dict) -> str:
    if result.get("skipped"):
        return f"skipped: {result['skipped']}"
    cut, align = result.get("cut", {}), result.get("align", {})
    uncovered = result.get("uncovered_share")
    return (f"cut-chin {fmt(cut.get('above_chin'))}  cut-joint {fmt(cut.get('above_joint'))}  "
            f"fitness {align.get('fitness', 0):.3f}  mean {align.get('mean_distance', 0):.4f}  "
            f"uncovered {'-' if uncovered is None else f'{100 * uncovered:.2f}%'}")


def head_fit(head: dict | None) -> str:
    if not head:
        return "-"
    if "error" in head:
        return head["error"]
    start = head["start_mean_distance"] + head["start_reverse_mean_distance"]
    final = head["mean_distance"] + head["reverse_mean_distance"]
    return f"{head['method']}, {start:.4f} → {final:.4f}"


def table(results: dict[str, dict]) -> str:
    rows = ["| Run | Cut − chin | Cut − joint | Align (method, fitness, mean, reverse) | Head fit (method, mean + reverse: start → final) | Uncovered texels | Skipped |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, result in results.items():
        cut, align = result.get("cut", {}), result.get("align")
        uncovered = result.get("uncovered_share")
        rows.append(
            f"| `{name}` | {fmt(cut.get('above_chin'))} | {fmt(cut.get('above_joint'))} | "
            + (f"{align['method']}, {align['fitness']:.3f}, {align['mean_distance']:.4f}, {align['reverse_mean_distance']:.4f}"
               if align else "-")
            + f" | {head_fit(result.get('head_fit'))}"
            + f" | {'-' if uncovered is None else f'{100 * uncovered:.2f} %'} | {result.get('skipped') or '-'} |")
    return "\n".join(rows)


def contact_sheet(pairs: list[Path], path: Path) -> None:
    from PIL import Image

    tiles = [Image.open(p) for p in pairs]
    columns = 2
    width, height = tiles[0].size
    sheet = Image.new("RGB", (columns * (width + 10), ((len(tiles) + 1) // columns) * (height + 10)), (20, 20, 20))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % columns) * (width + 10), (index // columns) * (height + 10)))
    sheet.save(path)


def evaluate(source: Path, work: Path) -> dict:
    try:
        rerun(source, work)
    except StepFailed as error:
        return {"error": str(error).splitlines()[0]}
    result = measure(work)
    (work / "eval.json").write_text(json.dumps(result, indent=2))
    subprocess.run([sys.executable, __file__, "--render", str(work)], check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    annotate(work, result)
    return result


def main(argv: list[str]) -> int:
    if argv[:1] == ["--render"]:
        render(Path(argv[1]))
        sys.stdout.flush()
        os._exit(0)  # bpy can hang in interpreter teardown
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--runs", type=Path, default=ROOT / "data" / "kit_runs")
    parser.add_argument("--only", help="comma-separated run folder names")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    runs = candidates(args.runs, only)
    if not runs:
        print(f"No run folder in {args.runs} has a skeleton and a sculpted head.", file=sys.stderr)
        return 2
    print(f"Evaluating {len(runs)} runs into {args.out}", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        results = dict(zip((r.name for r in runs), pool.map(lambda r: evaluate(r, args.out / r.name), runs)))
    (args.out / "summary.json").write_text(json.dumps(results, indent=2))
    pairs = [args.out / name / "pair.png" for name in results if (args.out / name / "pair.png").exists()]
    if pairs:
        contact_sheet(pairs, args.out / "sheet.png")
    print(table(results))
    return 0 if all("error" not in r for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
