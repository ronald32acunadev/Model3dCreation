"""Two-stage job pipeline: shape on one GPU, paint on the other."""

import asyncio

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


class Scheduler:
    """Moves jobs through shape then paint, so one job paints while the next shapes."""

    def __init__(self, store: JobStore, bus: EventBus, shape: StageRunner, paint: StageRunner):
        self._store = store
        self._bus = bus
        self._shape = shape
        self._paint = paint
        self._shape_queue: asyncio.Queue[str] = asyncio.Queue()
        self._paint_queue: asyncio.Queue[str] = asyncio.Queue()
        self._waiting: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self.start_error: JobError | None = None

    @property
    def ready(self) -> bool:
        """Whether both stage runners have their models loaded."""
        return self._shape.ready and self._paint.ready

    async def start(self) -> None:
        """Start both runners and the two stage loops; a failure is kept in start_error.

        The initial load is not retried: the failures seen so far (a placement OOM, missing
        weights) repeat on every attempt, so the engine reports them and waits for a fix.
        """
        try:
            await self._shape.start()
            await self._paint.start()
        except StageError as error:
            self.start_error = JobError(code=error.code, message=error.message)
            raise
        except Exception as error:
            self.start_error = JobError(code="internal_error", message=f"{type(error).__name__}: {error}")
            raise
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

    async def _shape_loop(self) -> None:
        while True:
            job_id = await self._shape_queue.get()
            if job_id in self._waiting:
                self._waiting.remove(job_id)
            record = self._store.get(job_id)
            if record is None or record.is_final:
                continue
            if record.cancel_requested:
                self._cancel(record)
                continue
            self._begin(record, JobState.shaping, "shape")
            result = await self._run_stage(record, self._shape, "shape")
            if result is None:
                continue
            if record.cancel_requested:
                self._cancel(record)
            elif not record.options.texture:
                model = result.output.replace(record.dir / MODEL_OUTPUT)
                self._succeed(record, StageResult(output=model, vertices=result.vertices, faces=result.faces), False)
            else:
                self._paint_queue.put_nowait(job_id)

    async def _paint_loop(self) -> None:
        while True:
            job_id = await self._paint_queue.get()
            record = self._store.get(job_id)
            if record is None or record.is_final:
                continue
            if record.cancel_requested:
                self._cancel(record)
                continue
            self._begin(record, JobState.painting, "paint")
            result = await self._run_stage(record, self._paint, "paint")
            if result is None:
                continue
            if record.cancel_requested:
                self._cancel(record)
            else:
                self._succeed(record, result, True)
