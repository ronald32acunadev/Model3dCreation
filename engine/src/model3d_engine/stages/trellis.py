"""TRELLIS.2 stage: image to a textured or untextured GLB through a separate worker.

The engine process removes the background with rembg and talks JSON lines to a worker that
runs under vendor/TRELLIS.2/.venv, where torch and the TRELLIS packages live. The worker is
hosted by a ProcessStageRunner. Heavy imports stay inside the class methods.
"""

import json
import os
import subprocess
from pathlib import Path
from subprocess import PIPE

from model3d_engine.stages.hunyuan import (
    BACKGROUND_MODEL,
    _has_transparency,
    _input_image,
    paint_face_target,
)
from model3d_engine.stages.protocol import (
    MODEL_OUTPUT,
    SHAPE_OUTPUT,
    ProgressCallback,
    StageError,
    StageResult,
    StageTask,
)

TRELLIS_FACTORY = "model3d_engine.stages.trellis:make_trellis_stage"
PIPELINE_TYPES = {"draft": "512", "final": "1024_cascade", "high": "1024_cascade"}
WORKER_SCRIPT = Path(__file__).with_name("trellis_worker.py")
TRELLIS_INPUT = "trellis_input.png"


def _parse(line: str) -> dict | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


class TrellisStage:
    """Image to GLB with TRELLIS.2, resident in a worker process on one GPU."""

    def __init__(
        self,
        trellis_dir: str,
        device: str,
        hf_home: str,
        python: str | None = None,
        worker_script: str | None = None,
        post_device: str | None = None,
    ):
        python = python if python is not None else f"{trellis_dir}/.venv/bin/python"
        worker_script = worker_script if worker_script is not None else str(WORKER_SCRIPT)
        if not Path(python).is_file():
            raise StageError(
                "generator_unavailable",
                f"TRELLIS.2 is not installed: {trellis_dir} has no .venv (see README.md, 'Alternative Generator: TRELLIS.2')",
            )
        for card in (device, post_device or device):
            if not card.startswith("cuda:"):
                raise StageError("generator_unavailable", f"TRELLIS.2 needs a CUDA device, got {card}")
        self._trellis_dir = trellis_dir
        self._rembg_session = None
        self._process = self._start(python, worker_script, self._worker_env(trellis_dir, device, hf_home, post_device))

    @staticmethod
    def _worker_env(trellis_dir: str, device: str, hf_home: str, post_device: str | None) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONHOME", None)
        env.update(
            {
                "TRELLIS_DIR": trellis_dir,
                "MODEL3D_TRELLIS_CONFIG_DIR": str(Path(hf_home) / "trellis2-pipeline"),
                "HF_HOME": hf_home,
                "HF_HUB_OFFLINE": "1",
                "PYTORCH_ALLOC_CONF": "expandable_segments:True",
                "ATTN_BACKEND": "xformers",
                "SPARSE_ATTN_BACKEND": "xformers",
                "OPENCV_IO_ENABLE_OPENEXR": "1",
                "PYTHONPATH": trellis_dir,
                "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                "CUDA_VISIBLE_DEVICES": device.split(":", 1)[1],
            }
        )
        if post_device is not None and post_device != device:
            # The worker sees the generation card as cuda:0 and decodes on cuda:1.
            env["CUDA_VISIBLE_DEVICES"] += "," + post_device.split(":", 1)[1]
            env["MODEL3D_TRELLIS_POST_DEVICE"] = "cuda:1"
        else:
            env.pop("MODEL3D_TRELLIS_POST_DEVICE", None)
        return env

    def _start(self, python: str, worker_script: str, env: dict[str, str]) -> subprocess.Popen:
        process = subprocess.Popen(
            [python, worker_script],
            stdin=PIPE,
            stdout=PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            cwd=self._trellis_dir,
            env=env,
        )
        while True:
            line = process.stdout.readline()
            if line == "":
                process.wait()
                raise StageError(
                    "worker_start_failed", f"TRELLIS.2 worker exited with code {process.returncode}"
                )
            message = _parse(line)
            if message is None:
                continue
            if message.get("type") == "ready":
                return process
            if message.get("type") == "error":
                process.wait()
                raise StageError("worker_start_failed", message["message"])

    def _remove_background(self, image):
        from rembg import new_session, remove

        if self._rembg_session is None:
            self._rembg_session = new_session(BACKGROUND_MODEL)
        return remove(image.convert("RGB"), session=self._rembg_session, bgcolor=[255, 255, 255, 0])

    def run(self, task: StageTask, progress: ProgressCallback) -> StageResult:
        """Generate SHAPE_OUTPUT, or MODEL_OUTPUT when textured, in the job folder."""
        from PIL import Image

        image = Image.open(_input_image(task.job_dir)).convert("RGBA")
        if task.options.remove_background and not _has_transparency(image):
            progress(0.0, "Removing the background")
            image = self._remove_background(image)
        input_path = task.job_dir / TRELLIS_INPUT
        image.save(input_path)
        textured = task.options.texture
        output = task.job_dir / (MODEL_OUTPUT if textured else SHAPE_OUTPUT)
        request = {
            "type": "run",
            "image": str(input_path.resolve()),
            "output": str(output.resolve()),
            "pipeline_type": PIPELINE_TYPES[task.options.quality.value],
            "seed": task.seed,
            "faces": paint_face_target(task.options),
            "texture": textured,
        }
        try:
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
        except BrokenPipeError:
            self._process.wait()
            raise StageError(
                "worker_crashed", f"TRELLIS.2 worker exited with code {self._process.returncode}"
            ) from None
        while True:
            line = self._process.stdout.readline()
            if line == "":
                self._process.wait()
                raise StageError(
                    "worker_crashed", f"TRELLIS.2 worker exited with code {self._process.returncode}"
                )
            message = _parse(line)
            if message is None:
                continue
            kind = message.get("type")
            if kind == "progress":
                progress(message["value"], message["message"])
            elif kind == "done":
                return StageResult(
                    output=output,
                    vertices=message["vertices"],
                    faces=message["faces"],
                    textured=textured,
                )
            elif kind == "error":
                raise StageError(message["code"], message["message"])

    def close(self) -> None:
        """Stop the worker; kill it when it does not exit within 5 seconds."""
        process = self._process
        if process.poll() is None:
            try:
                process.stdin.write(json.dumps({"type": "stop"}) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def make_trellis_stage(trellis_dir: str, device: str, hf_home: str, post_device: str | None = None) -> TrellisStage:
    """Factory used by the TRELLIS.2 shape worker process; post_device decodes and bakes on a second card."""
    return TrellisStage(trellis_dir, device, hf_home, post_device=post_device)
