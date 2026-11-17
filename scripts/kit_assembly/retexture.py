"""Retexture a model region by region from close-ups of its input image (TRELLIS.2 texturing).

    python scripts/kit_assembly/retexture.py RUN_DIR OUT.glb [--cards 0,1] [--resolution 1024]

RUN_DIR is a head-pass run folder: `mold.glb` (the TRELLIS.2 model), `mold_labels.npy` (its part
per vertex), `rig.json` and `crops/<part>.png` (each part's crop of the input image). Each part,
grown into its neighbours (regions.py), is textured by TRELLIS.2 from its own crop on the model's
UVs (texture_trellis.py, one process per card, every card at once), then the regions are blended
into the model's texture and OUT.glb is the model with only that texture replaced (glb_image.py).
A report goes to OUT.json. Run it with the engine's interpreter.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from glb_image import base_color_images, image_bytes, read_glb, replace_image, write_glb  # noqa: E402
from regions import blend, face_labels, split  # noqa: E402

TRELLIS_DIR = ROOT / "vendor" / "TRELLIS.2"


def trellis_env(card: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update({
        "TRELLIS_DIR": str(TRELLIS_DIR), "PYTHONPATH": str(TRELLIS_DIR),
        "HF_HOME": str(ROOT / "data" / "hf"), "HF_HUB_OFFLINE": "1",
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": card,
        "PYTORCH_ALLOC_CONF": "expandable_segments:True",
        "ATTN_BACKEND": "xformers", "SPARSE_ATTN_BACKEND": "xformers",
    })
    return env


def share(jobs: list[dict], sizes: list[int], cards: list[str]) -> list[list[dict]]:
    """Jobs per card, largest first to the least loaded card, so the cards finish together. The
    largest region (the head's crop at full size peaked at 10.3 GB) goes to the first card, which
    should be the one that does not drive the desktop."""
    loads = [0] * len(cards)
    shares: list[list[dict]] = [[] for _ in cards]
    for size, job in sorted(zip(sizes, jobs), key=lambda pair: -pair[0]):
        card = loads.index(min(loads))
        shares[card].append(job)
        loads[card] += size
    return shares


def retexture(run: Path, out: Path, cards: list[str], resolution: int, on_region=None) -> dict:
    rig = json.loads((run / "rig.json").read_text())
    names = rig["parts"]
    mesh = trimesh.load(run / "mold.glb", force="mesh")
    labels = face_labels(mesh, np.load(run / "mold_labels.npy"))
    work = out.with_suffix(".parts")
    work.mkdir(parents=True, exist_ok=True)
    jobs, sizes, painted_index = [], [], {}
    for region in split(mesh, labels, names):
        crop = run / "crops" / f"{region.name}.png"
        if not crop.exists():
            continue
        region.mesh.export(work / f"{region.name}.glb")
        jobs.append({"mesh": str(work / f"{region.name}.glb"), "image": str(crop), "output": str(work / f"{region.name}.png")})
        sizes.append(len(region.faces) * (2 if region.name == "head" else 1))  # the head's crop is the densest
        painted_index[region.name] = names.index(region.name)
    report: dict = {"regions": {}, "resolution": resolution}
    started = time.monotonic()
    document, binary = read_glb(run / "mold.glb")
    image = list(base_color_images(document).values())[0]
    base = np.asarray(Image.open(io.BytesIO(image_bytes(document, binary, image))).convert("RGBA"), np.float32) / 255
    processes = []
    for card, card_jobs in zip(cards, share(jobs, sizes, cards)):
        if not card_jobs:
            continue
        request = work / f"jobs_card{card}.json"
        request.write_text(json.dumps({"config_dir": str(ROOT / "data" / "hf" / f"trellis2-texturing-{card}"),
                                       "resolution": resolution, "texture_size": base.shape[0], "seed": 42,
                                       "jobs": card_jobs}))
        log = (work / f"card{card}.err").open("w")
        processes.append((card, log, subprocess.Popen(
            [str(TRELLIS_DIR / ".venv" / "bin" / "python"), str(TOOLS / "texture_trellis.py"), str(request)],
            env=trellis_env(card), stdout=subprocess.PIPE, stderr=log, text=True)))
    # Both cards work at once; their finished regions are read as they come.
    import selectors
    selector = selectors.DefaultSelector()
    for card, _, process in processes:
        selector.register(process.stdout, selectors.EVENT_READ, card)
    open_streams = len(processes)
    while open_streams:
        for key, _ in selector.select():
            line = key.fileobj.readline()
            if not line:
                selector.unregister(key.fileobj)
                open_streams -= 1
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "output" in message:
                name = Path(message["output"]).stem
                report["regions"][name] = {"card": key.data, "seconds": message["seconds"], "peak_gib": message["peak_gib"]}
                if on_region is not None:
                    on_region(name, len(report["regions"]), len(jobs))
            elif "loaded_seconds" in message:
                report.setdefault("load_seconds", {})[key.data] = message["loaded_seconds"]
    failures = []
    for card, log, process in processes:
        process.wait()
        log.close()
        if process.returncode != 0:
            failures.append(f"card {card}: " + " | ".join((work / f"card{card}.err").read_text().strip().splitlines()[-3:]))
    if failures:
        report["error"] = "\n".join(failures)
        return report
    painted = {}
    for name, index in painted_index.items():
        color = np.asarray(Image.open(work / f"{name}.png").convert("RGB"), np.float32) / 255
        painted[index] = np.dstack([color, base[:, :, 3]])
    result = blend(mesh, labels, len(names), base, painted)
    buffer = io.BytesIO()
    Image.fromarray((np.clip(result, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA").save(buffer, "PNG")
    document, binary = replace_image(document, binary, image, buffer.getvalue(), "image/png")
    write_glb(out, document, binary)
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cards", default="0", help="comma-separated CUDA card indexes (PCI bus order), worked at once")
    parser.add_argument("--resolution", type=int, default=1024, choices=(512, 1024))
    args = parser.parse_args(argv)
    report = retexture(args.run, args.out, args.cards.split(","), args.resolution)
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
