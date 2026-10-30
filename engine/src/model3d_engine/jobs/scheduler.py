"""Job pipeline: shape on one GPU and paint on the other, or both taking turns on one GPU,
plus an optional rig stage that adds a skeleton to finished models."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from model3d_contracts.events import CancelledEvent, CompletedEvent, FailedEvent, ProgressEvent, ResultStats
from model3d_contracts.jobs import JobError, JobState

from model3d_engine.jobs.events import EventBus, utcnow
from model3d_engine.jobs.store import JobRecord, JobStore
from model3d_engine.stages.protocol import (
    MODEL_OUTPUT,
    RIG_INPUT,
    ProgressCallback,
    StageError,
    StageResult,
    StageRunner,
    StageTask,
)


logger = logging.getLogger(__name__)


class Scheduler:
    """Moves jobs through shape then paint, so one job paints while the next shapes.

    Workers load for their task and unload when no task waits for them, so jobs in a row share
    one load. Stages on one device (or all of them in swap mode) never hold the card at the same
    time: a task unloads whatever else is loaded on its card before it loads its own worker.

    With swap=True both stages share one GPU and one loop runs them: the loaded stage drains its
    queue, then the card swaps to the other stage if it has work.

    Jobs with a skeleton go through `rig` last. `cards` maps "shape", "paint", and "rig" to a
    device; stages on the same device take turns, one task at a time.
    """

    def __init__(
        self,
        store: JobStore,
        bus: EventBus,
        shape: StageRunner,
        paint: StageRunner,
        swap: bool = False,
        rig: StageRunner | None = None,
        cards: dict[str, str] | None = None,
    ):
        self._store = store
        self._bus = bus
        self._shape = shape
        self._paint = paint
        self._rig = rig
        self._shape_queue: asyncio.Queue[str] = asyncio.Queue()
        self._paint_queue: asyncio.Queue[str] = asyncio.Queue()
        self._rig_queue: asyncio.Queue[str] = asyncio.Queue()
        locks: dict[str, asyncio.Lock] = {}
        cards = cards or {}
        # One lock per device; without a device each stage gets its own lock.
        self._card_lock = {name: locks.setdefault(cards.get(name, name), asyncio.Lock()) for name in ("shape", "paint", "rig")}
        self._waiting: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self._started = False
        self.start_error: JobError | None = None
        self._swap = swap
        self._work = asyncio.Event()  # swap mode: set when a queue gets a job

    @property
    def ready(self) -> bool:
        """Whether the engine can take jobs. No model needs to be loaded for that."""
        return self._started and self.start_error is None

    @property
    def rig_available(self) -> bool:
        """Whether jobs may ask for a skeleton."""
        return self._rig is not None and self._rig.ready

    async def start(self) -> None:
        """Start the stage loops. No model is loaded until a task needs it."""
        if self._rig is not None:
            await self._rig.start()
        if self._swap:
            self._tasks = [asyncio.create_task(self._swap_loop())]
        else:
            loops = [self._shape_loop(), self._paint_loop(), self._rig_loop()]
            self._tasks = [asyncio.create_task(loop) for loop in loops]
        self._started = True

    async def stop(self) -> None:
        """Stop the stage loops, then both runners."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._shape.stop()
        await self._paint.stop()
        if self._rig is not None:
            await self._rig.stop()

    def submit(self, record: JobRecord) -> None:
        """Queue a new job for the shape stage."""
        self._store.persist(record)
        self._waiting.append(record.job_id)
        self._shape_queue.put_nowait(record.job_id)
        self._work.set()

    def submit_rig(self, record: JobRecord) -> None:
        """Queue a rig-only job: its model is already in RIG_INPUT."""
        self._store.persist(record)
        self._rig_queue.put_nowait(record.job_id)
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
            if job_id in self._waiting:  # rig-only jobs never wait for shape
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

    def _shares_card(self, stage: str, other: str) -> bool:
        return self._swap or self._card_lock[stage] is self._card_lock[other]

    async def _free_card(self, stage: str) -> None:
        """Called holding the card: unload whatever else is loaded on it, so `stage` gets all of it."""
        for name, runner in (("shape", self._shape), ("paint", self._paint)):
            if name != stage and runner.ready and self._shares_card(stage, name):
                await runner.unload()

    async def _on_card(self, record: JobRecord, runner: StageRunner, stage: str) -> StageResult | None:
        """Run one stage task holding its card, loading its worker first if needed."""
        async with self._card_lock[stage]:
            try:
                await self._free_card(stage)
            except Exception as error:
                logger.exception("Could not free the %s card", stage)
                self._fail(record, "worker_unavailable", f"Could not free GPU memory for {stage}: {error}")
                return None
            if not runner.ready:
                self._progress(record, stage)(0.0, f"Loading {stage} model")
                try:
                    await runner.start()
                except StageError as error:
                    self._fail(record, error.code, error.message)
                    return None
                except Exception as error:
                    self._fail(record, "worker_unavailable", f"{stage} worker could not be loaded: {error}")
                    return None
                if record.cancel_requested:
                    self._cancel(record)
                    return None
            return await self._run_stage(record, runner, stage)

    async def _release(self, runner: StageRunner, queue: asyncio.Queue[str], stage: str) -> None:
        """Unload a stage's worker when no task waits for it. A failed unload is logged; the next load replaces it."""
        if not queue.empty() or not runner.ready:
            return
        async with self._card_lock[stage]:
            if queue.empty() and runner.ready:
                try:
                    await runner.unload()
                except Exception:
                    logger.exception("Could not unload the %s worker", stage)

    def _model_ready(self, record: JobRecord, result: StageResult, textured: bool) -> None:
        """A job's model is done: queue it for a skeleton, or finish it."""
        if not record.options.rig:
            self._succeed(record, result, textured)
            return
        result.output.replace(record.dir / RIG_INPUT)
        self._rig_queue.put_nowait(record.job_id)
        self._work.set()

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
        result = await self._on_card(record, self._shape, "shape")
        if result is None:
            return
        if record.cancel_requested:
            self._cancel(record)
        elif result.textured:
            self._model_ready(record, result, True)
        elif not record.options.texture:
            model = result.output.replace(record.dir / MODEL_OUTPUT)
            self._model_ready(record, StageResult(output=model, vertices=result.vertices, faces=result.faces), False)
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
        result = await self._on_card(record, self._paint, "paint")
        if result is None:
            return
        if record.cancel_requested:
            self._cancel(record)
        else:
            self._model_ready(record, result, True)

    async def _rig_job(self, job_id: str) -> None:
        record = self._store.get(job_id)
        if record is None or record.is_final:
            return
        if record.cancel_requested:
            self._cancel(record)
            return
        if self._rig is None:
            self._fail(record, "rig_unavailable", "This engine has no rig stage")
            return
        self._begin(record, JobState.rigging, "rig")
        async with self._card_lock["rig"]:
            try:
                await self._free_card("rig")
            except Exception as error:
                logger.exception("Could not free the rig card")
                self._fail(record, "worker_unavailable", f"Could not free GPU memory for rigging: {error}")
                return
            result = await self._run_stage(record, self._rig, "rig")
        if result is None:
            return
        if record.cancel_requested:
            self._cancel(record)
        else:
            self._succeed(record, result, record.options.texture)

    async def _guarded(self, job: Callable[[str], Awaitable[None]], job_id: str) -> None:
        """Run one job step; an unexpected error fails that job instead of ending its stage loop."""
        try:
            await job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Job %s hit an unexpected error", job_id)
            record = self._store.get(job_id)
            if record is not None and not record.is_final:
                self._fail(record, "internal_error", f"{type(error).__name__}: {error}")

    async def _shape_loop(self) -> None:
        while True:
            await self._guarded(self._shape_job, await self._shape_queue.get())
            await self._release(self._shape, self._shape_queue, "shape")

    async def _paint_loop(self) -> None:
        while True:
            await self._guarded(self._paint_job, await self._paint_queue.get())
            await self._release(self._paint, self._paint_queue, "paint")

    async def _rig_loop(self) -> None:
        while True:
            await self._guarded(self._rig_job, await self._rig_queue.get())

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

    async def _swap_loop(self) -> None:
        stages = (
            (self._shape, self._shape_queue, self._shape_job, "shape"),
            (self._paint, self._paint_queue, self._paint_job, "paint"),
        )
        while True:
            for queue, job in ((self._shape_queue, self._shape_job), (self._paint_queue, self._paint_job), (self._rig_queue, self._rig_job)):
                for settled in self._settle_queue(queue, job):
                    await settled
            for runner, queue, _job, stage in stages:
                await self._release(runner, queue, stage)
            if self._shape_queue.empty() and self._paint_queue.empty() and self._rig_queue.empty():
                self._work.clear()
                await self._work.wait()
                continue
            # A loaded stage drains its queue first, so jobs in a row share one load.
            step = next(((queue, job) for runner, queue, job, _stage in stages if runner.ready and not queue.empty()), None)
            if step is None and not self._rig_queue.empty():
                step = (self._rig_queue, self._rig_job)
            if step is None:
                step = next((queue, job) for _runner, queue, job, _stage in stages if not queue.empty())
            await self._guarded(step[1], step[0].get_nowait())
