"""Two-stage job pipeline: shape on one GPU and paint on the other, or both taking turns
on one GPU."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from model3d_contracts.events import CancelledEvent, CompletedEvent, FailedEvent, ProgressEvent, ResultStats
from model3d_contracts.jobs import JobError, JobState

from model3d_engine.jobs.events import EventBus, utcnow
from model3d_engine.jobs.store import JobRecord, JobStore
from model3d_engine.stages.protocol import (
    MODEL_OUTPUT,
    ProgressCallback,
    StageError,
    StageResult,
    StageRunner,
    StageTask,
)


logger = logging.getLogger(__name__)


class Scheduler:
    """Moves jobs through shape then paint, so one job paints while the next shapes.

    With swap=True both stages share one GPU and only one worker is loaded at a time: the
    loaded stage drains its queue, then the card swaps to the other stage if it has work.
    """

    def __init__(self, store: JobStore, bus: EventBus, shape: StageRunner, paint: StageRunner, swap: bool = False):
        self._store = store
        self._bus = bus
        self._shape = shape
        self._paint = paint
        self._shape_queue: asyncio.Queue[str] = asyncio.Queue()
        self._paint_queue: asyncio.Queue[str] = asyncio.Queue()
        self._waiting: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self.start_error: JobError | None = None
        self._swap = swap
        self._loaded: StageRunner | None = None  # swap mode: the runner holding the GPU
        self._swapping = False
        self._work = asyncio.Event()  # swap mode: set when a queue gets a job

    @property
    def ready(self) -> bool:
        """Whether the engine can take jobs: both runners loaded, or in swap mode the loaded one
        (or a swap in progress)."""
        if self._swap:
            return self._swapping or (self._loaded is not None and self._loaded.ready)
        return self._shape.ready and self._paint.ready

    async def start(self) -> None:
        """Start both runners and the two stage loops; a failure is kept in start_error.

        The initial load is not retried: the failures seen so far (a placement OOM, missing
        weights) repeat on every attempt, so the engine reports them and waits for a fix.
        """
        try:
            await self._shape.start()
            if self._swap:
                self._loaded = self._shape
            else:
                await self._paint.start()
        except StageError as error:
            self.start_error = JobError(code=error.code, message=error.message)
            raise
        except Exception as error:
            self.start_error = JobError(code="internal_error", message=f"{type(error).__name__}: {error}")
            raise
        if self._swap:
            self._tasks = [asyncio.create_task(self._swap_loop())]
        else:
            self._tasks = [asyncio.create_task(self._shape_loop()), asyncio.create_task(self._paint_loop())]

    async def stop(self) -> None:
        """Stop the stage loops, then both runners."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._shape.stop()
        await self._paint.stop()

    def submit(self, record: JobRecord) -> None:
        """Queue a new job for the shape stage."""
        self._store.persist(record)
        self._waiting.append(record.job_id)
        self._shape_queue.put_nowait(record.job_id)
        self._work.set()

    def queue_position(self, job_id: str) -> int | None:
        """Zero-based position among jobs waiting for shape, or None once it started."""
        return self._waiting.index(job_id) if job_id in self._waiting else None

    def cancel(self, job_id: str) -> bool:
        """Cancel a job: at once when queued, otherwise at its next stage boundary."""
        record = self._store.get(job_id)
        if record is None or record.is_final:
            return False
        if record.state == JobState.queued:
            self._waiting.remove(job_id)
            self._cancel(record)
        else:
            record.cancel_requested = True
        return True

    def _progress(self, record: JobRecord, stage: str) -> ProgressCallback:
        def callback(value: float, message: str) -> None:
            record.progress = value
            self._bus.publish(
                ProgressEvent(job_id=record.job_id, ts=utcnow(), stage=stage, progress=value, message=message)
            )

        return callback

    def _fail(self, record: JobRecord, code: str, message: str) -> None:
        record.transition(JobState.failed, error=JobError(code=code, message=message))
        self._store.persist(record)
        self._bus.publish(FailedEvent(job_id=record.job_id, ts=utcnow(), error=record.error))

    def _cancel(self, record: JobRecord) -> None:
        record.transition(JobState.cancelled)
        self._store.persist(record)
        self._bus.publish(CancelledEvent(job_id=record.job_id, ts=utcnow()))

    def _succeed(self, record: JobRecord, result: StageResult, textured: bool) -> None:
        record.transition(JobState.succeeded)
        self._store.persist(record)
        stats = ResultStats(
            vertices=result.vertices,
            faces=result.faces,
            textured=textured,
            duration_s=(record.finished_at - record.created_at).total_seconds(),
        )
        self._bus.publish(
            CompletedEvent(
                job_id=record.job_id, ts=utcnow(), result_url=f"/v1/jobs/{record.job_id}/result", stats=stats
            )
        )

    async def _run_stage(self, record: JobRecord, runner: StageRunner, stage: str) -> StageResult | None:
        task = StageTask(job_id=record.job_id, job_dir=record.dir, options=record.options, seed=record.seed)
        try:
            return await runner.run(task, self._progress(record, stage))
        except StageError as error:
            self._fail(record, error.code, error.message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fail(record, "internal_error", str(error))
        return None

    def _begin(self, record: JobRecord, state: JobState, stage: str) -> None:
        record.transition(state)
        self._store.persist(record)
        self._progress(record, stage)(0.0, f"Starting {stage}")

    async def _shape_job(self, job_id: str) -> None:
        if job_id in self._waiting:
            self._waiting.remove(job_id)
        record = self._store.get(job_id)
        if record is None or record.is_final:
            return
        if record.cancel_requested:
            self._cancel(record)
            return
        self._begin(record, JobState.shaping, "shape")
        result = await self._run_stage(record, self._shape, "shape")
        if result is None:
            return
        if record.cancel_requested:
            self._cancel(record)
        elif not record.options.texture:
            model = result.output.replace(record.dir / MODEL_OUTPUT)
            self._succeed(record, StageResult(output=model, vertices=result.vertices, faces=result.faces), False)
        else:
            self._paint_queue.put_nowait(job_id)
            self._work.set()

    async def _paint_job(self, job_id: str) -> None:
        record = self._store.get(job_id)
        if record is None or record.is_final:
            return
        if record.cancel_requested:
            self._cancel(record)
            return
        self._begin(record, JobState.painting, "paint")
        result = await self._run_stage(record, self._paint, "paint")
        if result is None:
            return
        if record.cancel_requested:
            self._cancel(record)
        else:
            self._succeed(record, result, True)

    async def _shape_loop(self) -> None:
        while True:
            await self._shape_job(await self._shape_queue.get())

    async def _paint_loop(self) -> None:
        while True:
            await self._paint_job(await self._paint_queue.get())

    def _needs_run(self, job_id: str) -> bool:
        record = self._store.get(job_id)
        return record is not None and not record.is_final and not record.cancel_requested

    def _settle_queue(self, queue: asyncio.Queue[str], job: Callable[[str], Awaitable[None]]) -> list[Awaitable[None]]:
        """Pop queued jobs that will not run (cancelled or gone), so they never cause a swap."""
        pending, settled = [], []
        while not queue.empty():
            job_id = queue.get_nowait()
            (pending if self._needs_run(job_id) else settled).append(job_id)
        for job_id in pending:
            queue.put_nowait(job_id)
        return [job(job_id) for job_id in settled]

    async def _load(self, runner: StageRunner) -> bool:
        """Swap mode: free the GPU from the loaded runner and load `runner`, retrying with the
        runner's restart delays. Returns whether it loaded."""
        self._swapping = True
        try:
            if self._loaded is not None:
                await self._loaded.unload()
                self._loaded = None
            for delay in (0.0, *getattr(runner, "restart_delays", ())):
                await asyncio.sleep(delay)
                try:
                    await runner.start()
                except StageError as error:
                    logger.error("Could not load the %s worker: %s", "shape" if runner is self._shape else "paint", error)
                    continue
                self._loaded = runner
                return True
            return False
        finally:
            self._swapping = False

    async def _swap_loop(self) -> None:
        stages = {self._shape: (self._shape_queue, self._shape_job), self._paint: (self._paint_queue, self._paint_job)}
        while True:
            for queue, job in stages.values():
                for settled in self._settle_queue(queue, job):
                    await settled
            if self._shape_queue.empty() and self._paint_queue.empty():
                self._work.clear()
                await self._work.wait()
                continue
            loaded_queue, loaded_job = stages[self._loaded] if self._loaded is not None else (None, None)
            if loaded_queue is not None and not loaded_queue.empty():
                await loaded_job(loaded_queue.get_nowait())
                continue
            target = self._paint if self._loaded is self._shape else self._shape
            if self._loaded is None and not self._shape_queue.empty():
                target = self._shape
            if await self._load(target):
                continue
            # The stage could not load: fail its waiting jobs, then take the other stage back so
            # the engine stays ready for new work.
            queue = stages[target][0]
            while not queue.empty():
                record = self._store.get(queue.get_nowait())
                if record is not None and not record.is_final:
                    name = "shape" if target is self._shape else "paint"
                    self._fail(record, "worker_unavailable", f"{name} worker could not be loaded")
            await self._load(self._paint if target is self._shape else self._shape)
