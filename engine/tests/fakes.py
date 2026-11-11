"""In-process stand-ins for the GPU stage runners."""

import asyncio
import json
import time
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

import trimesh

from model3d_engine.stages.head_pass import HeadPassFailed, HeadPassResult
from model3d_engine.stages.protocol import ProgressCallback, StageError, StageResult, StageTask


class FakeRunner:
    """Writes a small box GLB named after the stage and reports progress 0.5 and 1.0."""

    def __init__(
        self,
        output_name: str,
        delay: float = 0.0,
        fail_with: StageError | None = None,
        log: list[tuple[str, str]] | None = None,
        start_error: StageError | None = None,
    ):
        self.output_name = output_name
        self.delay = delay
        self.fail_with = fail_with
        self.start_error = start_error
        self.ready = False
        self.runs: list[tuple[str, float, float]] = []  # (job_id, start, end)
        self.log = log if log is not None else []  # ("load" | "unload", output_name), shared between runners

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.log.append(("load", self.output_name))
        self.ready = True

    async def unload(self) -> None:
        self.log.append(("unload", self.output_name))
        self.ready = False

    async def stop(self) -> None:
        self.ready = False

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult:
        start = time.monotonic()
        on_progress(0.5, "halfway")
        await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            self.runs.append((task.job_id, start, time.monotonic()))
            raise self.fail_with
        mesh = trimesh.creation.box()
        output = task.job_dir / self.output_name
        mesh.export(output)
        on_progress(1.0, "done")
        self.runs.append((task.job_id, start, time.monotonic()))
        return StageResult(output=output, vertices=len(mesh.vertices), faces=len(mesh.faces))


class FakeVoiceRunner(FakeRunner):
    """Writes a short silent 24 kHz mono WAV named speech.wav and reports progress 0.5 and 1.0."""

    def __init__(self, delay: float = 0.0, fail_with: StageError | None = None, log: list[tuple[str, str]] | None = None):
        super().__init__("speech.wav", delay=delay, fail_with=fail_with, log=log)

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult:
        start = time.monotonic()
        on_progress(0.5, "sentence 1 of 2")
        await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            self.runs.append((task.job_id, start, time.monotonic()))
            raise self.fail_with
        request = json.loads((task.job_dir / "speech_request.json").read_text())
        transcribe = request["type"] == "transcribe"
        if transcribe and b"short" in Path(request["ref_audio"]).read_bytes():
            self.runs.append((task.job_id, start, time.monotonic()))
            raise StageError("invalid_audio", "Recordings must be 3 to 60 seconds long")
        output = task.job_dir / ("reference.wav" if transcribe else self.output_name)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00" * 2400)
        if transcribe:
            (task.job_dir / "transcript.json").write_text(json.dumps({"text": "Hola, soy yo."}))
        on_progress(1.0, "sentence 2 of 2")
        self.runs.append((task.job_id, start, time.monotonic()))
        return StageResult(output=output, vertices=0, faces=0)


class FakeHeadPass:
    reason = None

    def __init__(
        self,
        model_dir: Path,
        mode: str = "applied",
        during: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.model = model_dir / "head_model.glb"
        self.mode = mode
        self.during = during
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, job_id: str, on_step: Callable[[str], None]) -> HeadPassResult:
        self.calls.append(job_id)
        self.started.set()
        on_step("step one")
        if self.during is not None:
            await self.during(job_id)
        if self.mode == "wait":
            await self.cancelled.wait()
            raise HeadPassFailed("terminated")
        if self.mode == "fail":
            raise HeadPassFailed("boom: bad thing\nmore output")
        if self.mode == "skipped":
            return HeadPassResult(False, None, "no usable head")
        trimesh.creation.icosphere().export(self.model)
        return HeadPassResult(True, self.model, None)

    async def cancel(self, job_id: str) -> None:
        self.cancelled.set()
