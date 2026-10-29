"""Starts the engine when none runs, follows its health, and stops it only when the app started it."""

import logging
import time
from collections.abc import Callable
from enum import Enum

from model3d_contracts.health import Health
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from model3d_client.paths import AppPaths
from model3d_client.services.engine_api import EngineApi

logger = logging.getLogger(__name__)
READY_POLL_MS = 10000
STOP_GRACE_MS = 15000


class EngineState(str, Enum):
    stopped = "stopped"
    starting = "starting"  # our process runs, the port does not answer yet (GPU detection)
    loading = "loading"  # the engine answers and loads its models
    ready = "ready"
    error = "error"  # the engine reports a start failure, or never answered
    crashed = "crashed"  # the engine we started exited


class EngineSupervisor(QObject):
    state_changed = Signal(object)
    health_changed = Signal(object)

    def __init__(
        self,
        api: EngineApi,
        paths: AppPaths,
        program: list[str] | None = None,
        poll_ms: int = 2000,
        start_timeout_s: float = 300,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._api = api
        self._paths = paths
        self._program = program or [str(paths.engine_python), "-m", "model3d_engine"]
        self._poll_ms = poll_ms
        self._start_timeout_s = start_timeout_s
        self._state = EngineState.stopped
        self._health: Health | None = None
        self._process: QProcess | None = None
        self._spawned_at = 0.0
        self._stopping = False
        self._on_stopped: Callable[[], None] | None = None
        self.owned = False
        self.failure: str | None = None  # why the engine we started is not running
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def health(self) -> Health | None:
        return self._health

    def start(self) -> None:
        """Use the engine that answers, or start one."""
        self.failure = None
        self._stopping = False
        self._api.health(self._on_first_health)

    def restart(self) -> None:
        self.stop(self.start)

    def stop(self, on_stopped: Callable[[], None]) -> None:
        """Stop the engine if the app started it; an external engine keeps running."""
        self._timer.stop()
        if self.owned and self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._stopping = True
            self._on_stopped = on_stopped
            self._process.terminate()  # SIGTERM: uvicorn stops the stage workers
            process = self._process
            QTimer.singleShot(STOP_GRACE_MS, lambda: self._kill_if_running(process))
            return
        self._set_state(EngineState.stopped)
        on_stopped()

    def _on_first_health(self, health: Health | None) -> None:
        if health is not None:
            self.owned = False
            self._apply(health)
        else:
            self._spawn()
        self._timer.start(self._poll_ms)

    def _spawn(self) -> None:
        self._paths.engine_log.parent.mkdir(parents=True, exist_ok=True)
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("MODEL3D_HOME", str(self._paths.data))
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(self._paths.repo))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setStandardOutputFile(str(self._paths.engine_log), QProcess.OpenModeFlag.Append)
        # Bound methods, not lambdas: Qt drops the connections when the supervisor is destroyed.
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_errored)
        self._process = process
        self.owned = True
        self._spawned_at = time.monotonic()
        logger.info("Starting the engine: %s", " ".join(self._program))
        process.start(self._program[0], self._program[1:])
        self._set_state(EngineState.starting)

    def _poll(self) -> None:
        self._api.health(self._on_health)

    def _on_health(self, health: Health | None) -> None:
        if self._stopping:
            return
        if health is not None:
            self._apply(health)
            return
        running = self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning
        if self.owned and running:
            if time.monotonic() - self._spawned_at > self._start_timeout_s:
                self.failure = f"The engine did not answer within {self._start_timeout_s:.0f} s"
                self._process.kill()  # _on_exit sees the failure and does not report a crash
                self._set_health(None)
                self._set_state(EngineState.error)
                self._timer.stop()
            else:
                self._set_state(EngineState.starting)
        elif not self.owned:
            # An external engine went away.
            self._set_health(None)
            self._set_state(EngineState.stopped)

    def _apply(self, health: Health) -> None:
        self._set_health(health)
        if health.error is not None:
            self._set_state(EngineState.error)
        elif health.ready:
            self._set_state(EngineState.ready)
        else:
            self._set_state(EngineState.loading)
        self._timer.setInterval(READY_POLL_MS if self._state == EngineState.ready else self._poll_ms)

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._on_exit(self.sender(), code)

    def _on_errored(self, error: QProcess.ProcessError) -> None:
        self._on_process_error(self.sender(), error)

    def _on_exit(self, process: QProcess, code: int) -> None:
        if process is not self._process:
            return
        self._process = None
        if self._stopping:
            self._stopping = False
            self.owned = False
            self._set_health(None)
            self._set_state(EngineState.stopped)
            if self._on_stopped is not None:
                callback, self._on_stopped = self._on_stopped, None
                callback()
            return
        if self._state == EngineState.error and self.failure:
            return  # killed after the start timeout
        logger.warning("The engine exited with code %s", code)
        failure = f"The engine exited with code {code}; see {self._paths.engine_log}"
        # Another engine may have taken the port while ours started: use it if it answers.
        self._api.health(lambda health: self._after_exit(health, failure))

    def _after_exit(self, health: Health | None, failure: str) -> None:
        if health is not None:
            self.owned = False
            self._apply(health)
            self._timer.start()
            return
        self.owned = False
        self.failure = failure
        self._timer.stop()
        self._set_health(None)
        self._set_state(EngineState.crashed)

    def _on_process_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and process is self._process:
            self._process = None
            self.owned = False
            self.failure = f"The engine could not be started: {process.errorString()}"
            self._timer.stop()
            self._set_state(EngineState.crashed)

    def _kill_if_running(self, process: QProcess) -> None:
        if process.state() != QProcess.ProcessState.NotRunning:
            logger.warning("The engine did not stop within %d s; killing it", STOP_GRACE_MS // 1000)
            process.kill()

    def _set_state(self, state: EngineState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _set_health(self, health: Health | None) -> None:
        self._health = health
        self.health_changed.emit(health)
