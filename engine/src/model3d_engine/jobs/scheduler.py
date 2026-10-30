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

    With swap=True both stages share one GPU and only one worker is loaded at a time: the
    loaded stage drains its queue, then the card swaps to the other stage if it has work.

    Jobs with a skeleton go through `rig` last. `cards` maps "shape", "paint", and "rig" to a
    device; stages on the same device take turns, one task at a time. When the rig card has no
    room for a task, the workers on it are parked (unloaded) and loaded again before their next
    task; a parked worker still counts as ready.
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
        self._parked: set[int] = set()  # id() of runners unloaded to make room for rigging
        self._rigging = False
        self._swap_parked = False  # swap mode: rigging unloaded the loaded worker; the next job loads one
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
            return (
                self._swapping
                or self._rigging
                or self._swap_parked
                or (self._loaded is not None and self._loaded.ready)
            )
        return all(runner.ready or id(runner) in self._parked for runner in (self._shape, self._paint))

    @property
    def rig_available(self) -> bool:
        """Whether jobs may ask for a skeleton."""
        return self._rig is not None and self._rig.ready

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
        if self._rig is not None:
            await self._rig.start()
        if self._swap:
            self._tasks = [asyncio.create_task(self._swap_loop())]
        else:
            loops = [self._shape_loop(), self._paint_loop(), self._rig_loop()]
            self._tasks = [asyncio.create_task(loop) for loop in loops]

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

    async def _on_card(self, record: JobRecord, runner: StageRunner, stage: str) -> StageResult | None:
        """Run one stage task holding its card, loading the worker again if it was parked."""
        async with self._card_lock[stage]:
            if id(runner) in self._parked:
                try:
                    await runner.start()
                except Exception as error:
                    # Still parked: the next task tries again (the card may free up, as after rigging).
                    message = error.message if isinstance(error, StageError) else str(error)
                    self._fail(record, "worker_unavailable", f"{stage} worker could not be loaded again: {message}")
                    return None
                self._parked.discard(id(runner))
            return await self._run_stage(record, runner, stage)

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
            self._rigging = True  # before parking, so health never reports a gap
            try:
                try:
                    await self._make_room_for_rig()
                except Exception as error:
                    logger.exception("Could not free the rig card")
                    self._fail(record, "worker_unavailable", f"Could not free GPU memory for rigging: {error}")
                    return
                result = await self._run_stage(record, self._rig, "rig")
            finally:
                self._rigging = False
        if result is None:
            return
        if record.cancel_requested:
            self._cancel(record)
        else:
            self._succeed(record, result, record.options.texture)

    async def _make_room_for_rig(self) -> None:
        """Called holding the rig card: park the workers on it when the rig task would not fit."""
        if await self._rig.fits():
            return
        if self._swap:
            if self._loaded is not None:
                try:
                    await self._loaded.unload()
                except Exception:
                    logger.exception("Could not unload the %s worker", self._name(self._loaded))
                self._loaded = None
                self._swap_parked = True
            return
        for stage, runner in (("shape", self._shape), ("paint", self._paint)):
            if self._card_lock[stage] is self._card_lock["rig"] and runner.ready:
                await runner.unload()
                self._parked.add(id(runner))

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

    async def _paint_loop(self) -> None:
        while True:
            await self._guarded(self._paint_job, await self._paint_queue.get())

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

    def _name(self, runner: StageRunner) -> str:
        return "shape" if runner is self._shape else "paint"

    async def _load(self, runner: StageRunner) -> bool:
        """Swap mode: free the GPU from the loaded runner and load `runner`, retrying with the
        runner's restart delays. Any error counts as a failed attempt. Returns whether it loaded."""
        self._swapping = True
        try:
            if self._loaded is not None:
                try:
                    await self._loaded.unload()
                except Exception:
                    logger.exception("Could not unload the %s worker", self._name(self._loaded))
                self._loaded = None
            for delay in (0.0, *getattr(runner, "restart_delays", ())):
                await asyncio.sleep(delay)
                try:
                    await runner.start()
                except Exception as error:
                    logger.error("Could not load the %s worker: %s", self._name(runner), error)
                    continue
                self._loaded = runner
                self._swap_parked = False
                return True
            return False
        finally:
            self._swapping = False

    def _fail_queue(self, queue: asyncio.Queue[str], message: str) -> None:
        while not queue.empty():
            job_id = queue.get_nowait()
            if job_id in self._waiting:
                self._waiting.remove(job_id)
            record = self._store.get(job_id)
            if record is not None and not record.is_final:
                self._fail(record, "worker_unavailable", message)

    async def _swap_loop(self) -> None:
        stages = {self._shape: (self._shape_queue, self._shape_job), self._paint: (self._paint_queue, self._paint_job)}
        while True:
            for queue, job in (*stages.values(), (self._rig_queue, self._rig_job)):
                for settled in self._settle_queue(queue, job):
                    await settled
            if self._shape_queue.empty() and self._paint_queue.empty() and self._rig_queue.empty():
                self._work.clear()
                await self._work.wait()
                continue
            loaded_queue, loaded_job = stages[self._loaded] if self._loaded is not None else (None, None)
            if loaded_queue is not None and not loaded_queue.empty():
                await self._guarded(loaded_job, loaded_queue.get_nowait())
                continue
            if not self._rig_queue.empty():
                # Rigging needs no worker loaded; it parks the loaded one only when the card is full.
                await self._guarded(self._rig_job, self._rig_queue.get_nowait())
                continue
            if self._shape_queue.empty() and self._paint_queue.empty():
                continue
            if self._loaded is None:
                target = self._shape if not self._shape_queue.empty() else self._paint
            else:
                target = self._paint if self._loaded is self._shape else self._shape
            if await self._load(target):
                continue
            # The stage could not load: fail its waiting jobs, then take the other stage back so
            # the engine stays ready for new work.
            self._fail_queue(stages[target][0], f"{self._name(target)} worker could not be loaded")
            other = self._paint if target is self._shape else self._shape
            if await self._load(other):
                continue
            # Neither worker loads: report it like a failed start, so health explains the state
            # and submissions get 503 with the cause.
            message = "Neither the shape nor the paint worker could be loaded; see the engine log"
            self._fail_queue(self._shape_queue, message)
            self._fail_queue(self._paint_queue, message)
            self.start_error = JobError(code="worker_unavailable", message=message)
            return
