"""In-process stand-ins for the GPU stage runners."""

import asyncio
import time

import trimesh

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
        fits_result: bool = True,
    ):
        self.output_name = output_name
        self.delay = delay
        self.fail_with = fail_with
        self.start_error = start_error
        self.ready = False
        self.runs: list[tuple[str, float, float]] = []  # (job_id, start, end)
        self.log = log if log is not None else []  # ("load" | "unload", output_name), shared between runners
        self.restart_delays: tuple[float, ...] = ()
        self.fits_result = fits_result

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

    async def fits(self) -> bool:
        """Rig runner only: whether its card has room without parking a worker."""
        return self.fits_result

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
