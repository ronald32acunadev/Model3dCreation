"""TRELLIS.2 worker: runs under vendor/TRELLIS.2/.venv and speaks JSON lines on stdout.

TrellisStage (model3d_engine.stages.trellis) starts this script. It must not import
model3d_engine, model3d_contracts or pydantic, because it runs in another interpreter.
"""

import gc
import json
import os
import sys
import threading
import time
from pathlib import Path

for _key, _value in (
    ("OPENCV_IO_ENABLE_OPENEXR", "1"),
    ("PYTORCH_ALLOC_CONF", "expandable_segments:True"),
    ("ATTN_BACKEND", "xformers"),
    ("SPARSE_ATTN_BACKEND", "xformers"),
):
    os.environ.setdefault(_key, _value)

# Protocol lines go to a private copy of the real stdout; library prints go to stderr.
PROTO = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)

# The raw mesh is cut to this many faces before baking: well above any job's face target,
# small enough that CuMesh's BVH and remeshing fit next to it on a 12 GB card.
RAW_FACES = 2_000_000
# Where latents are decoded and the mesh is baked: a second card when the engine gives one.
SPLIT = "MODEL3D_TRELLIS_POST_DEVICE" in os.environ
POST_DEVICE = os.environ.get("MODEL3D_TRELLIS_POST_DEVICE", "cuda:0")
PHASE_PROGRESS = {
    "get_cond": (0.05, "Reading the image"),
    "sample_sparse_structure": (0.1, "Sampling the structure"),
    "sample_shape_slat": (0.3, "Generating the shape"),
    "sample_shape_slat_cascade": (0.3, "Generating the shape"),
    "sample_tex_slat": (0.6, "Generating the texture"),
    "decode_latent": (0.8, "Decoding the mesh"),
}


def send(message: dict) -> None:
    PROTO.write(json.dumps(message) + "\n")
    PROTO.flush()


def exit_with_parent(parent: int) -> None:
    """Die with the engine's proxy process, so unloading the stage frees the GPU."""
    while True:
        time.sleep(1)
        if os.getppid() != parent:
            os._exit(1)


class NoRembg:
    """Inputs arrive with alpha, so the gated RMBG-2.0 model is never needed."""

    def __init__(self, **_kwargs):
        pass

    def to(self, _device):
        return self

    def cpu(self):
        return self

    def __call__(self, image):
        return image.convert("RGBA")


def write_config(snapshot: Path, config_dir: Path) -> None:
    """A pipeline folder that reuses the cached checkpoints and runs with low_vram."""
    config_dir.mkdir(parents=True, exist_ok=True)
    args = json.loads((snapshot / "pipeline.json").read_text())
    args["args"]["low_vram"] = True
    (config_dir / "pipeline.json").write_text(json.dumps(args))
    link = config_dir / "ckpts"
    if not os.path.lexists(link):
        link.symlink_to(snapshot / "ckpts")


def guard_fill_holes() -> None:
    """Give CuMesh the cached VRAM before decoding fills holes, and skip the fill if it still runs out.

    CuMesh allocates outside PyTorch's cache, so after the decoders run on a dense model the cache
    starves it; an unfilled hole is a small flaw, losing the whole job is not.
    """
    import torch
    from trellis2.representations.mesh.base import Mesh

    original = Mesh.fill_holes

    def fill_holes(self, *args, **kwargs):
        torch.cuda.empty_cache()
        try:
            return original(self, *args, **kwargs)
        except RuntimeError as error:
            if "out of memory" not in str(error):
                raise
            torch.cuda.empty_cache()
            print(f"Filling holes ran out of VRAM, keeping the mesh as decoded: {error}", file=sys.stderr)

    Mesh.fill_holes = fill_holes


def load_pipeline():
    import trellis2.pipelines.rembg as rembg

    rembg.BiRefNet = NoRembg
    guard_fill_holes()
    from huggingface_hub import snapshot_download
    from trellis2.pipelines import Trellis2ImageTo3DPipeline

    snapshot = Path(snapshot_download("microsoft/TRELLIS.2-4B"))
    config_dir = Path(os.environ["MODEL3D_TRELLIS_CONFIG_DIR"])
    write_config(snapshot, config_dir)
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(config_dir))
    pipeline.cuda()
    for name, (value, text) in PHASE_PROGRESS.items():
        original = getattr(pipeline, name)

        def wrapped(*args, _original=original, _value=value, _text=text, **kwargs):
            send({"type": "progress", "value": _value, "message": _text})
            return _original(*args, **kwargs)

        setattr(pipeline, name, wrapped)
    if SPLIT:
        decode_on_post_device(pipeline)
    return pipeline


def decode_on_post_device(pipeline) -> None:
    """Decode on the second card, so decoders and CuMesh get a card of their own.

    The flow models stay on the generation card; only the latents cross over.
    """
    import torch

    original = pipeline.decode_latent

    def decode_latent(shape_slat, tex_slat, resolution):
        generation = pipeline.device
        torch.cuda.empty_cache()
        pipeline._device = torch.device(POST_DEVICE)
        try:
            with torch.cuda.device(POST_DEVICE):
                return original(shape_slat.to(POST_DEVICE), tex_slat.to(POST_DEVICE), resolution)
        finally:
            pipeline._device = generation

    pipeline.decode_latent = decode_latent


def run_job(pipeline, message: dict) -> dict:
    import torch
    from PIL import Image

    image = Image.open(message["image"])
    mesh = pipeline.run(image, seed=message["seed"], pipeline_type=message["pipeline_type"])[0]
    with torch.cuda.device(POST_DEVICE):
        return bake_glb(mesh, message)


def bake_glb(mesh, message: dict) -> dict:
    """Simplify, remesh, unwrap, and bake on the card that holds the decoded mesh."""
    import o_voxel
    import torch
    import trimesh

    # CuMesh allocates outside PyTorch's cache, so hand the cached blocks back to CUDA first.
    torch.cuda.empty_cache()
    mesh.simplify(RAW_FACES)
    torch.cuda.empty_cache()
    send({"type": "progress", "value": 0.9, "message": "Baking the texture"})

    def bake(remesh: bool):
        return o_voxel.postprocess.to_glb(
            vertices=mesh.vertices,
            faces=mesh.faces,
            attr_volume=mesh.attrs,
            coords=mesh.coords,
            attr_layout=mesh.layout,
            voxel_size=mesh.voxel_size,
            aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=message["faces"],
            texture_size=message.get("texture_size", 4096),
            remesh=remesh,
            remesh_band=1,
            remesh_project=0,
        )

    try:
        glb = bake(remesh=True)
    except RuntimeError as error:
        if "out of memory" not in str(error):
            raise
        # Remeshing is the hungriest step on dense models; the plain path still bakes the texture.
        torch.cuda.empty_cache()
        print(f"Remeshing ran out of VRAM, baking without it: {error}", file=sys.stderr)
        glb = bake(remesh=False)
    if not message["texture"]:
        glb.visual = trimesh.visual.ColorVisuals(glb)
    glb.export(message["output"], extension_webp=False)
    torch.cuda.empty_cache()
    return {"type": "done", "vertices": len(glb.vertices), "faces": len(glb.faces)}


def park(pipeline, torch) -> None:
    """Move every sub-model back to RAM: with low_vram, a failure mid-stage leaves one on the GPU."""
    for model in pipeline.models.values():
        model.cpu()
    pipeline.image_cond_model.cpu()
    # Tensors of the failed job are only freed once their reference cycles are collected.
    gc.collect()
    torch.cuda.empty_cache()


def main() -> None:
    threading.Thread(target=exit_with_parent, args=(os.getppid(),), daemon=True).start()
    try:
        pipeline = load_pipeline()
    except Exception as error:
        send({"type": "error", "code": "worker_start_failed", "message": f"{type(error).__name__}: {error}"})
        os._exit(1)
    import torch

    send({"type": "ready"})
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("type") == "stop":
            os._exit(0)
        if message.get("type") != "run":
            continue
        try:
            send(run_job(pipeline, message))
        except torch.OutOfMemoryError as error:
            park(pipeline, torch)
            send({"type": "error", "code": "gpu_out_of_memory", "message": f"TRELLIS.2 ran out of VRAM: {error}"})
        except Exception as error:
            park(pipeline, torch)
            send({"type": "error", "code": "generation_failed", "message": f"{type(error).__name__}: {error}"})
    os._exit(0)


if __name__ == "__main__":
    main()
