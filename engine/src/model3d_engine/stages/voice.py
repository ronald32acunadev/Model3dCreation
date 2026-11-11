"""Speech stage: text to a WAV through a Qwen3-TTS worker on one GPU.

VoiceStage starts voice_worker.py under vendor/Qwen3-TTS/.venv and talks JSON lines to it.
The worker is hosted by a ProcessStageRunner named voice. The worker's imports stay in its
own interpreter, so this module needs no torch.
"""

import json
import os
import subprocess
from pathlib import Path
from subprocess import PIPE

from model3d_engine.stages.protocol import ProgressCallback, StageError, StageResult, StageTask

REQUEST_FILE = "speech_request.json"
SPEECH_OUTPUT = "speech.wav"
TRANSCRIBE = "transcribe"
REFERENCE_OUTPUT = "reference.wav"  # a transcribe job's clip, resampled to 24 kHz mono
TRANSCRIPT_OUTPUT = "transcript.json"
WORKER_SCRIPT = Path(__file__).with_name("voice_worker.py")


def _parse(line: str) -> dict | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


class VoiceStage:
    """Text to speech with Qwen3-TTS, resident in a worker process on one GPU."""

    def __init__(
        self,
        qwen_dir: Path,
        device: str,
        hf_home: Path,
        python: str | None = None,
        worker_script: str | None = None,
    ):
        python = python if python is not None else str(qwen_dir / ".venv" / "bin" / "python")
        worker_script = worker_script if worker_script is not None else str(WORKER_SCRIPT)
        if not Path(python).is_file():
            raise StageError("voice_unavailable", f"Qwen3-TTS is not installed: {python} is missing")
        if not device.startswith("cuda:"):
            raise StageError("voice_unavailable", f"Qwen3-TTS needs a CUDA device, got {device}")
        self._qwen_dir = qwen_dir
        self._process = self._start(python, worker_script, self._worker_env(qwen_dir, device, hf_home))

    @staticmethod
    def _worker_env(qwen_dir: Path, device: str, hf_home: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONHOME", None)
        env.update(
            {
                "HF_HOME": str(hf_home),
                "HF_HUB_OFFLINE": "1",
                "PYTHONPATH": str(qwen_dir),
                "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                "CUDA_VISIBLE_DEVICES": device.split(":", 1)[1],
            }
        )
        return env

    def _start(self, python: str, worker_script: str, env: dict[str, str]) -> subprocess.Popen:
        process = subprocess.Popen(
            [python, worker_script],
            stdin=PIPE,
            stdout=PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            cwd=str(self._qwen_dir),
            env=env,
        )
        while True:
            line = process.stdout.readline()
            if line == "":
                process.wait()
                raise StageError(
                    "worker_start_failed", f"Qwen3-TTS worker exited with code {process.returncode}"
                )
            message = _parse(line)
            if message is None:
                continue
            if message.get("type") == "ready":
                return process
            if message.get("type") == "error":
                process.wait()
                raise StageError("worker_start_failed", message["message"])

    @staticmethod
    def _read_request(path: Path) -> dict:
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise StageError("invalid_request", f"{path.name} is missing or unreadable: {error}") from None
        if not isinstance(request, dict):
            raise StageError("invalid_request", f"{path.name} must hold a JSON object")
        return request

    def run(self, task: StageTask, progress: ProgressCallback) -> StageResult:
        """Write the job's audio (and a transcript for a transcribe job) in its folder from the speech request."""
        request = self._read_request(task.job_dir / REQUEST_FILE)
        mode = request.get("type")
        output = task.job_dir / (REFERENCE_OUTPUT if mode == TRANSCRIBE else SPEECH_OUTPUT)
        # The request's own "type" (speak, design or transcribe) travels as "mode"; "type" is the protocol verb.
        message = {**request, "type": "run", "mode": mode, "output": str(output)}
        if mode == TRANSCRIBE:
            message["transcript"] = str(task.job_dir / TRANSCRIPT_OUTPUT)
        try:
            self._process.stdin.write(json.dumps(message) + "\n")
            self._process.stdin.flush()
        except BrokenPipeError:
            self._process.wait()
            raise StageError(
                "worker_crashed", f"Qwen3-TTS worker exited with code {self._process.returncode}"
            ) from None
        while True:
            line = self._process.stdout.readline()
            if line == "":
                self._process.wait()
                raise StageError(
                    "worker_crashed", f"Qwen3-TTS worker exited with code {self._process.returncode}"
                )
            message = _parse(line)
            if message is None:
                continue
            kind = message.get("type")
            if kind == "progress":
                progress(message["value"], message["message"])
            elif kind == "done":
                return StageResult(output=output, vertices=0, faces=0)
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


def make_voice_stage(qwen_dir: str, device: str, hf_home: str) -> VoiceStage:
    """Factory used by the voice worker process."""
    return VoiceStage(Path(qwen_dir), device, Path(hf_home))
