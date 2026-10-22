"""Hunyuan3D 2.1 shape and paint stages.

Heavy imports (torch, bpy, PIL, trimesh, Hunyuan3D) happen inside the stage classes,
which only run in worker processes; the API process imports this module for
stage_env() and the factory names without loading any of them.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from model3d_contracts.jobs import JobOptions

from model3d_engine.config import Settings
from model3d_engine.stages.protocol import (
    MODEL_OUTPUT,
    RIG_FACES,
    SHAPE_OUTPUT,
    ProgressCallback,
    StageError,
    StageResult,
    StageTask,
)

HUNYUAN_MODEL = "tencent/Hunyuan3D-2.1"
# Quality preset -> (num_inference_steps, octree_resolution). "final" is upstream's default
# (measured 77 s on GPU0); "draft" is this project's choice.
QUALITY_PRESETS = {"final": (50, 384), "draft": (25, 256), "high": (50, 384)}
# Faces the paint stage keeps (it decimates the shape before unwrapping and baking). Upstream
# always cuts to 40000; "high" keeps 150000: +24 s paint and +0.6 GiB VRAM, measured 2026-10-22.
PAINT_FACES = {"draft": 40_000, "final": 40_000, "high": 150_000}
PATCHED_LINE = ".to(pbr_hs.device)"


def stage_env(settings: Settings) -> dict[str, str]:
    """Environment every stage worker needs before it imports torch."""
    return {
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "HF_HOME": str(settings.hf_home),
        "HF_HUB_OFFLINE": "1",
        "U2NET_HOME": str(settings.home / "u2net"),
        "PYTORCH_ALLOC_CONF": "expandable_segments:True",
    }


@dataclass(frozen=True)
class PaintSettings:
    """How the paint worker runs a job: per-view resolution, views, and memory savers."""

    resolution: int
    max_views: int
    split_guidance: bool  # run the three classifier-free-guidance branches one at a time
    offload: bool  # move the paint pipeline's sub-models to RAM between uses


def paint_settings(options: JobOptions) -> PaintSettings:
    """768 px per view for the high level; it fits a 12 GB card only with both memory savers
    (peak 7.2 GiB against 9.3 GiB at 512 px; docs/spikes/paint-high-detail.md)."""
    high = options.quality.value == "high"
    return PaintSettings(resolution=768 if high else 512, max_views=6, split_guidance=high, offload=high)


def paint_face_target(options: JobOptions) -> int:
    """Faces the paint stage keeps: the job's target_faces, else its quality level's."""
    if options.target_faces is not None:
        return options.target_faces
    return PAINT_FACES[options.quality.value]


def shape_face_target(options: JobOptions) -> int | None:
    """Faces the shape stage keeps: target_faces, else the rig cap for untextured rig jobs.

    Untextured jobs are otherwise not decimated (up to about 860,000 faces); rigging was
    measured on meshes of at most RIG_FACES.
    """
    if options.target_faces is not None:
        return options.target_faces
    if options.rig and not options.texture:
        return RIG_FACES
    return None


def _input_image(job_dir: Path) -> Path:
    matches = sorted(job_dir.glob("input.*"))
    if not matches:
        raise StageError("invalid_image", "Job has no input image")
    return matches[0]


def _has_transparency(image) -> bool:
    return image.mode == "RGBA" and image.getchannel("A").getextrema()[0] < 255


def _add_paths(*paths: str) -> None:
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)


class ShapeStage:
    """Image to untextured mesh with Hunyuan3D-Shape, resident on one GPU."""

    def __init__(self, hunyuan_dir: str, device: str):
        import torch

        _add_paths(hunyuan_dir, f"{hunyuan_dir}/hy3dshape")
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
        from hy3dshape.rembg import BackgroundRemover

        self.torch = torch
        self.device = device
        # Hunyuan creates some tensors on plain "cuda", which means the current device.
        torch.cuda.set_device(device)
        self.pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(HUNYUAN_MODEL, device=device)
        self.rembg = BackgroundRemover()

    def run(self, task: StageTask, progress: ProgressCallback) -> StageResult:
        """Generate SHAPE_OUTPUT in the job folder."""
        from PIL import Image

        torch = self.torch
        torch.cuda.set_device(self.device)
        image = Image.open(_input_image(task.job_dir)).convert("RGBA")
        if task.options.remove_background and not _has_transparency(image):
            image = self.rembg(image.convert("RGB"))
        steps, octree = QUALITY_PRESETS[task.options.quality.value]

        def on_step(step_idx, _t, _outputs):
            progress((step_idx + 1) / steps, f"Denoising step {step_idx + 1} of {steps}")

        try:
            mesh = self.pipeline(
                image=image,
                num_inference_steps=steps,
                octree_resolution=octree,
                generator=torch.Generator(device=self.device).manual_seed(task.seed),
                callback=on_step,
                callback_steps=1,
            )[0]
        except torch.OutOfMemoryError as error:
            raise StageError("gpu_out_of_memory", f"Shape stage ran out of VRAM on {self.device}: {error}") from error
        target = shape_face_target(task.options)
        if target and len(mesh.faces) > target:
            mesh = mesh.simplify_quadric_decimation(target)
        output = task.job_dir / SHAPE_OUTPUT
        mesh.export(output)
        progress(1.0, "Shape ready")
        return StageResult(output=output, vertices=len(mesh.vertices), faces=len(mesh.faces))


def make_shape_stage(hunyuan_dir: str, device: str) -> ShapeStage:
    """Factory used by the shape worker process."""
    return ShapeStage(hunyuan_dir, device)


def _check_attention_patch(hunyuan_dir: str) -> None:
    """Fail fast unless both copies of the paint attention processor are patched."""
    from huggingface_hub import snapshot_download

    snapshot = Path(
        snapshot_download(HUNYUAN_MODEL, allow_patterns=["hunyuan3d-paintpbr-v2-1/*"], local_files_only=True)
    )
    for path in (
        Path(hunyuan_dir) / "hy3dpaint/hunyuanpaintpbr/unet/attn_processor.py",
        snapshot / "hunyuan3d-paintpbr-v2-1/unet/attn_processor.py",
    ):
        if PATCHED_LINE not in path.read_text():
            raise RuntimeError(
                f"{path} is not patched; apply engine/patches/hunyuan3d-paint-device.patch (docs/setup.md step 5)"
            )


class PaintStage:
    """Mesh to PBR-textured GLB with Hunyuan3D-Paint.

    Diffusion and the VAE run on `device`, with the VAE decoding one view at a time.
    DINOv2 runs on `helper_device`: "cpu" in production, because the shape worker keeps
    about 8.7 GiB of the other GPU (measured in phase 2). A GPU helper also takes over the
    VAE decode, which is how the phase 1 spike ran.
    """

    def __init__(self, hunyuan_dir: str, device: str, helper_device: str):
        import bpy  # noqa: F401  # must load before pymeshlab: both ship libembree4.so.4
        import torch

        self.torch = torch
        self.device = device
        _check_attention_patch(hunyuan_dir)
        # Upstream config paths are relative to the repository root.
        os.chdir(hunyuan_dir)
        _add_paths(hunyuan_dir, f"{hunyuan_dir}/hy3dshape", f"{hunyuan_dir}/hy3dpaint")
        from torchvision_fix import apply_fix

        apply_fix()
        from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline

        torch.cuda.set_device(device)
        conf = Hunyuan3DPaintConfig(max_num_view=6, resolution=512)
        conf.device = device
        conf.realesrgan_ckpt_path = f"{hunyuan_dir}/hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
        conf.multiview_cfg_path = f"{hunyuan_dir}/hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
        conf.custom_pipeline = f"{hunyuan_dir}/hy3dpaint/hunyuanpaintpbr"
        self.paint = Hunyuan3DPaintPipeline(conf)

        multiview = self.paint.models["multiview_model"]
        vae = multiview.pipeline.vae
        # Decode the 12 views (6 x albedo/metallic-roughness) one at a time: decoding them
        # as one batch is the activation peak that overflowed the paint GPU in phase 1.
        vae.enable_slicing()
        if helper_device == "cpu":
            # DINOv2 runs once per job and returns a small embedding; on the CPU (float32)
            # it costs seconds and keeps both GPUs free for shape and diffusion.
            multiview.dino_v2 = multiview.dino_v2.to(device="cpu", dtype=torch.float32)
            dino_forward = multiview.dino_v2.forward
            multiview.dino_v2.forward = lambda images: dino_forward(images).to(device=device, dtype=torch.float16)
        else:
            multiview.dino_v2 = multiview.dino_v2.to(helper_device)
            dino_forward = multiview.dino_v2.forward
            multiview.dino_v2.forward = lambda images: dino_forward(images).to(device)
            vae_decode = vae.decode

            def decode_on_helper(z, *args, **kwargs):
                vae.to(helper_device)
                try:
                    return vae_decode(z.to(helper_device), *args, **kwargs)
                finally:
                    vae.to(device)

            vae.decode = decode_on_helper

        pipeline = multiview.pipeline
        self._pipeline = pipeline
        # The paint UNet uses learned text tokens, so the text encoder (0.64 GiB) is never called.
        self._park_text_encoder()
        # Upstream builds tensors on pipeline.device and vae.device, which report "cpu" under
        # offload hooks: pin the pipeline device and move the VAE's input explicitly.
        exec_device = torch.device(device)
        type(pipeline).device = property(lambda _pipeline: exec_device)
        encode_images = pipeline.encode_images
        pipeline.encode_images = lambda images: encode_images(images.to(exec_device))
        self._split = False
        self._install_split_guidance(pipeline.unet)
        self._settings = PaintSettings(resolution=512, max_views=6, split_guidance=False, offload=False)
        torch.cuda.empty_cache()

    def _park_text_encoder(self) -> None:
        if getattr(self._pipeline, "text_encoder", None) is not None:
            self._pipeline.text_encoder.to("cpu")

    def _install_split_guidance(self, unet) -> None:
        """Let the UNet run its classifier-free-guidance batch one branch at a time.

        The batch stacks three independent branches (unconditional, reference, full); only the
        batch dimension mixes them, so running them in turn gives the same result with about a
        third of the activation memory.
        """
        torch = self.torch
        forward = unet.forward

        def split_forward(sample, timestep, encoder_hidden_states, *args, **kwargs):
            if not self._split:
                return forward(sample, timestep, encoder_hidden_states, *args, **kwargs)
            batch = sample.shape[0]
            kwargs.pop("cache", None)
            outputs = []
            for index in range(batch):
                part = slice(index, index + 1)
                chunk = {
                    key: value[part]
                    if isinstance(value, torch.Tensor) and value.dim() > 0 and value.shape[0] == batch
                    else value
                    for key, value in kwargs.items()
                }
                output = forward(sample[part], timestep, encoder_hidden_states[part], *args, **chunk)
                outputs.append(output[0] if isinstance(output, tuple) else output)
            return (torch.cat(outputs, dim=0),)

        unet.forward = split_forward

    def _apply(self, settings: PaintSettings) -> None:
        """Reconfigure the worker for a job's settings; switching levels costs a few seconds."""
        if settings == self._settings:
            return
        torch = self.torch
        self.paint.config.resolution = settings.resolution
        self.paint.config.max_selected_view_num = settings.max_views
        self._split = settings.split_guidance
        if settings.offload and not self._settings.offload:
            self._pipeline.enable_model_cpu_offload(gpu_id=torch.device(self.device).index)
        elif self._settings.offload and not settings.offload:
            self._pipeline.remove_all_hooks()
            self._pipeline.to(self.device)
            self._park_text_encoder()
        torch.cuda.empty_cache()
        self._settings = settings

    def run(self, task: StageTask, progress: ProgressCallback) -> StageResult:
        """Texture SHAPE_OUTPUT and write MODEL_OUTPUT in the job folder."""
        import trimesh

        torch = self.torch
        torch.cuda.set_device(self.device)
        from utils.simplify_mesh_utils import mesh_simplify_trimesh

        self._apply(paint_settings(task.options))

        progress(0.05, "Remeshing and generating views")
        try:
            # Simplify here, to the job's face target, instead of upstream's fixed 40000.
            remeshed = task.job_dir / "white_mesh_remesh.obj"
            mesh_simplify_trimesh(
                str(task.job_dir / SHAPE_OUTPUT), str(remeshed), target_count=paint_face_target(task.options)
            )
            self.paint(
                mesh_path=str(remeshed),
                image_path=str(_input_image(task.job_dir)),
                output_mesh_path=str(task.job_dir / "textured.obj"),
                use_remesh=False,
            )
        except torch.OutOfMemoryError as error:
            raise StageError("gpu_out_of_memory", f"Paint stage ran out of VRAM: {error}") from error
        progress(0.95, "Exporting")
        model = (task.job_dir / "textured.glb").replace(task.job_dir / MODEL_OUTPUT)
        mesh = trimesh.load(model, force="mesh")
        progress(1.0, "Model ready")
        return StageResult(output=model, vertices=len(mesh.vertices), faces=len(mesh.faces))


def make_paint_stage(hunyuan_dir: str, device: str, helper_device: str) -> PaintStage:
    """Factory used by the paint worker process."""
    return PaintStage(hunyuan_dir, device, helper_device)
