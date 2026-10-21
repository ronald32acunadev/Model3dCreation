"""Runs one pipeline stage in its own spawned process, so each GPU stage keeps its CUDA
context, import order, and crashes to itself."""

import asyncio
import importlib
import logging
import multiprocessing
import os
import time

from model3d_engine.stages.protocol import ProgressCallback, StageError, StageResult, StageTask

logger = logging.getLogger(__name__)


def worker_main(factory: str, kwargs: dict, env: dict[str, str], conn) -> None:
    """Worker process entry point: build the stage once, then serve run requests."""
    # The environment must be in place before the factory module (and torch) is imported.
    os.environ.update(env)
    module_name, attr_name = factory.split(":", 1)
    try:
        stage = getattr(importlib.import_module(module_name), attr_name)(**kwargs)
    except Exception as error:
        conn.send(("error", "worker_start_failed", f"{type(error).__name__}: {error}"))
        os._exit(1)
    conn.send(("ready",))
    while True:
        try:
            message = conn.recv()
        except EOFError:
            os._exit(0)
        if message[0] == "stop":
            # os._exit skips interpreter teardown, which can hang in native threads.
            os._exit(0)
        if message[0] == "run":
            try:
                result = stage.run(message[1], lambda value, text: conn.send(("progress", value, text)))
                conn.send(("done", result))
            except StageError as error:
                conn.send(("error", error.code, error.message))
            except Exception as error:
                conn.send(("error", "internal_error", f"{type(error).__name__}: {error}"))


class _WorkerDied(Exception):
    def __init__(self, exitcode: int | None):
        super().__init__(f"exit code {exitcode}")
        self.exitcode = exitcode


def _receive(conn, process, timeout: float | None):
    """Blocking receive that notices a dead worker; runs in an executor thread."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if conn.poll(0.2):
            try:
                return conn.recv()
            except EOFError:
                process.join(5)
                raise _WorkerDied(process.exitcode) from None
        if not process.is_alive():
            if conn.poll(0):
                return conn.recv()
            raise _WorkerDied(process.exitcode)
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError


class ProcessStageRunner:
    """StageRunner that hosts its stage in a worker process and restarts it after a crash."""

    def __init__(
        self,
        factory: str,
        kwargs: dict,
        env: dict[str, str],
        name: str,
        start_timeout_s: float = 600.0,
        restart_delays: tuple[float, ...] = (0.0, 5.0, 30.0, 120.0),
        task_timeout_s: float = 900.0,
    ):
        self.factory = factory
        self.kwargs = kwargs
        self.env = env
        self.name = name
        self.start_timeout_s = start_timeout_s
        self.restart_delays = restart_delays
        self.task_timeout_s = task_timeout_s
        self.ready = False
        self._process = None
        self._conn = None
        self._lock: asyncio.Lock | None = None
        self._restart: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        """Spawn the worker and wait until its model is loaded."""
        self._lock = self._lock or asyncio.Lock()
        await self._spawn()

    async def _spawn(self) -> None:
        if self._conn is not None:
            self._conn.close()  # the previous worker's pipe
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        process = ctx.Process(
            target=worker_main,
            args=(self.factory, self.kwargs, self.env, child_conn),
            daemon=True,
            name=f"model3d-{self.name}",
        )
        process.start()
        child_conn.close()
        self._process, self._conn = process, parent_conn
        loop = asyncio.get_running_loop()
        try:
            message = await loop.run_in_executor(None, _receive, parent_conn, process, self.start_timeout_s)
        except _WorkerDied as died:
            raise StageError(
                "worker_start_failed", f"{self.name} worker exited with code {died.exitcode} while loading"
            ) from None
        except TimeoutError:
            process.kill()
            raise StageError(
                "worker_start_failed", f"{self.name} worker did not load within {self.start_timeout_s:.0f} s"
            ) from None
        if message[0] == "error":
            await loop.run_in_executor(None, process.join, 5)
            raise StageError(message[1], message[2])
        self.ready = True

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult:
        """Run one task in the worker, forwarding its progress.

        A worker that crashed, during a task or while idle, is restarted and the task waits for it.
        A task that outlasts task_timeout_s is failed and its stuck worker killed and restarted.
        """
        async with self._lock:
            if self._restart is not None and not self._restart.done():
                await self._restart
            if self.ready and not self._process.is_alive():
                # The worker died between jobs (kernel OOM kill, driver fault): restart it now.
                self.ready = False
                self._restart = asyncio.create_task(self._respawn())
                await self._restart
            if not self.ready:
                raise StageError("worker_unavailable", f"{self.name} worker could not be restarted")
            try:
                self._conn.send(("run", task))
            except OSError:
                self.ready = False
                if not self._stopping:
                    self._restart = asyncio.create_task(self._respawn())
                raise StageError("worker_crashed", f"{self.name} worker was gone before the task started") from None
            loop = asyncio.get_running_loop()
            deadline = time.monotonic() + self.task_timeout_s
            while True:
                remaining = deadline - time.monotonic()
                try:
                    message = await loop.run_in_executor(None, _receive, self._conn, self._process, remaining)
                except TimeoutError:
                    await self._kill_stuck_worker()
                    raise StageError(
                        "stage_timeout", f"{self.name} did not finish within {self.task_timeout_s:.0f} s"
                    ) from None
                except _WorkerDied as died:
                    self.ready = False
                    if not self._stopping:
                        self._restart = asyncio.create_task(self._respawn())
                    raise StageError(
                        "worker_crashed", f"{self.name} worker exited with code {died.exitcode}"
                    ) from None
                if message[0] == "progress":
                    on_progress(message[1], message[2])
                elif message[0] == "done":
                    return message[1]
                elif message[0] == "error":
                    raise StageError(message[1], message[2])

    async def _kill_stuck_worker(self) -> None:
        """Kill a worker that stopped answering mid-task, then restart it like a crash."""
        self.ready = False
        self._process.kill()
        await asyncio.get_running_loop().run_in_executor(None, self._process.join, 5)
        if not self._stopping:
            self._restart = asyncio.create_task(self._respawn())

    async def _respawn(self) -> None:
        for delay in self.restart_delays:
            await asyncio.sleep(delay)
            if self._stopping:
                return
            try:
                await self._spawn()
                return
            except StageError as error:
                logger.error("%s worker failed to restart: %s", self.name, error.message)
        logger.error("%s worker gave up after %d restart attempts", self.name, len(self.restart_delays))

    async def unload(self) -> None:
        """Stop the worker to free its GPU, after any running task; start() loads it again."""
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            await self._cancel_restart()
            await self._stop_process()

    async def stop(self) -> None:
        """Stop the worker for good, killing it if it does not exit within 5 s."""
        self._stopping = True
        await self._cancel_restart()
        await self._stop_process()

    async def _cancel_restart(self) -> None:
        if self._restart is not None:
            self._restart.cancel()
            try:
                await self._restart
            except (asyncio.CancelledError, StageError):
                pass
            self._restart = None

    async def _stop_process(self) -> None:
        self.ready = False
        if self._process is not None and self._process.is_alive():
            try:
                self._conn.send(("stop",))
            except OSError:
                pass
            await asyncio.get_running_loop().run_in_executor(None, self._process.join, 5)
            if self._process.is_alive():
                self._process.kill()
                await asyncio.get_running_loop().run_in_executor(None, self._process.join, 5)
        if self._conn is not None:
            self._conn.close()
            self._conn = None
