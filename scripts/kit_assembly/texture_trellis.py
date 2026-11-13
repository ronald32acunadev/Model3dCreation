"""Texture meshes on their own UVs with TRELLIS.2's texturing pipeline, loaded once.

Runs in TRELLIS.2's interpreter (`vendor/TRELLIS.2/.venv/bin/python`) with `TRELLIS_DIR`, `HF_HOME`
(the engine's `data/hf`) and one visible CUDA card:

    python texture_trellis.py JOBS.json

JOBS.json is `{"config_dir": DIR, "resolution": 1024, "texture_size": 4096, "seed": 42,
"jobs": [{"mesh": GLB, "image": PNG, "output": PNG}, ...]}`. For each job the mesh (with UVs) is
textured from the image and its base color is written as OUTPUT in the mesh's own UV layout,
holes filled. One JSON line per finished job goes to stdout: `{"output", "seconds", "peak_gib"}`.

The pipeline's own `run` unwraps new UVs and builds a new mesh; here the color field is sampled at
the given mesh's texels instead (as its `postprocess_mesh` does), so the texture fits the model.
"""

import gc
import json
import os
import sys
import time
from pathlib import Path


CONTEXT = None  # one nvdiffrast context for every job


class NoRembg:
    """Region crops arrive with alpha, so the gated RMBG-2.0 model is never loaded."""

    def __init__(self, **_kwargs):
        pass

    def to(self, _device):
        return self

    def cpu(self):
        return self

    def __call__(self, image):
        return image.convert("RGBA")


def load(config_dir: Path):
    import trellis2.pipelines.rembg as rembg

    rembg.BiRefNet = NoRembg
    from huggingface_hub import snapshot_download
    from trellis2.pipelines import Trellis2TexturingPipeline

    snapshot = Path(snapshot_download("microsoft/TRELLIS.2-4B"))
    args = json.loads((snapshot / "texturing_pipeline.json").read_text())
    args["args"]["low_vram"] = True
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "pipeline.json").write_text(json.dumps(args))
    link = config_dir / "ckpts"
    if os.path.islink(link) and os.readlink(link) != str(snapshot / "ckpts"):
        link.unlink()
    if not os.path.lexists(link):
        link.symlink_to(snapshot / "ckpts")
    pipeline = Trellis2TexturingPipeline.from_pretrained(str(config_dir))
    pipeline.cuda()
    return pipeline


def base_color(pipeline, vertices, faces, uv, voxels, resolution: int, size: int):
    """The voxel color field sampled at every texel of the mesh's UVs (rows top-down, as glTF images)."""
    import cv2
    import flex_gemm
    import numpy as np
    import nvdiffrast.torch as dr
    import torch

    v = torch.from_numpy(vertices).float().cuda()
    f = torch.from_numpy(faces).int().cuda()
    flipped = uv.copy()
    flipped[:, 1] = 1 - flipped[:, 1]
    u = torch.from_numpy(flipped).float().cuda()
    u = torch.cat([u * 2 - 1, torch.zeros_like(u[:, :1]), torch.ones_like(u[:, :1])], dim=-1).unsqueeze(0)
    # nvdiffrast allocates outside PyTorch's cache: hand the cached blocks back to CUDA first.
    torch.cuda.empty_cache()
    global CONTEXT
    if CONTEXT is None:
        CONTEXT = dr.RasterizeCudaContext()
    rast, _ = dr.rasterize(CONTEXT, u, f, resolution=[size, size])
    mask = rast[0, ..., 3] > 0
    position = dr.interpolate(v.unsqueeze(0), rast, f)[0][0]
    attributes = torch.zeros(size, size, voxels.shape[1], device="cuda")
    attributes[mask] = flex_gemm.ops.grid_sample.grid_sample_3d(
        voxels.feats, voxels.coords, shape=torch.Size([*voxels.shape, *voxels.spatial_shape]),
        grid=((position[mask] + 0.5) * resolution).reshape(1, -1, 3), mode="trilinear")
    color = np.clip(attributes[..., pipeline.pbr_attr_layout["base_color"]].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    holes = (~mask.cpu().numpy()).astype(np.uint8)
    return cv2.inpaint(color, holes, 3, cv2.INPAINT_TELEA)


def texture(pipeline, job: dict, resolution: int, size: int, seed: int) -> None:
    import numpy as np
    import torch
    import trimesh
    from PIL import Image

    mesh = trimesh.load(job["mesh"], force="mesh")
    image = pipeline.preprocess_image(Image.open(job["image"]).convert("RGBA"))
    normalized = pipeline.preprocess_mesh(mesh)
    torch.manual_seed(seed)
    condition = pipeline.get_cond([image], 512 if resolution == 512 else 1024)
    shape = pipeline.encode_shape_slat(normalized, resolution)
    flow = pipeline.models["tex_slat_flow_model_512" if resolution == 512 else "tex_slat_flow_model_1024"]
    latent = pipeline.sample_tex_slat(condition, flow, shape, {})
    voxels = pipeline.decode_tex_slat(latent)
    del condition, shape, latent
    color = base_color(pipeline, np.asarray(normalized.vertices), np.asarray(normalized.faces),
                       np.asarray(mesh.visual.uv), voxels, resolution, size)
    del voxels
    Image.fromarray(color).save(job["output"])


def main(argv: list[str]) -> int:
    import torch

    request = json.loads(Path(argv[1]).read_text())
    torch.set_grad_enabled(False)
    started = time.monotonic()
    pipeline = load(Path(request["config_dir"]))
    print(json.dumps({"loaded_seconds": round(time.monotonic() - started, 1)}), flush=True)
    for job in request["jobs"]:
        torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        texture(pipeline, job, request.get("resolution", 1024), request.get("texture_size", 4096), request.get("seed", 42))
        gc.collect()
        torch.cuda.empty_cache()
        print(json.dumps({"output": job["output"], "seconds": round(time.monotonic() - started, 1),
                          "peak_gib": round(torch.cuda.max_memory_reserved() / 2**30, 2)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
