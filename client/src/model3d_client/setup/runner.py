"""Runs install steps in order, records each finished step with its fingerprint, and resumes or repairs from install.json."""

import errno
import os
import signal
import subprocess
import threading
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from model3d_client.setup.state import InstallState, StepRecord, load_state, save_state

LOG_FILE = Path("data/logs/install.log")
KILL_AFTER_S = 10.0
NO_SPACE = "No space left on device"


@dataclass(frozen=True)
class Command:
    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)  # merged over the app's environment


@dataclass
class StepContext:
    install_dir: Path
    log: Callable[[str], None]
    cancelled: Callable[[], bool]
    run: Callable[[Command], None]


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    component: str  # "base" or "skeletons"
    size_bytes: int
    fingerprint: str
    actions: list[Command | Callable[[StepContext], None]]
    done_check: Callable[[Path], bool]


class StepFailed(Exception):
    """A step that could not finish, with a stable code: no_space, command_failed, network, cancelled."""

    def __init__(self, code: str, message: str, step_id: str):
        super().__init__(f"{step_id}: {code}: {message}")
        self.code = code
        self.message = message
        self.step_id = step_id


def pending_steps(steps: list[Step], state: InstallState | None, install_dir: Path) -> list[Step]:
    """Steps never recorded, recorded with another fingerprint, or whose result is gone."""
    records = state.steps if state is not None else {}
    return [
        step for step in steps
        if step.id not in records or records[step.id].fingerprint != step.fingerprint or not step.done_check(install_dir)
    ]


class StepRunner:
    def __init__(self, install_dir: Path, steps: list[Step], app_version: str, components: list[str],
                 on_progress: Callable[[str, str, float], None], on_log: Callable[[str], None]):
        self.install_dir = install_dir
        self.steps = steps
        self.app_version = app_version
        self.components = components
        self._on_progress = on_progress
        self._on_log = on_log
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._current = ""

    def run(self) -> None:
        """Run every pending step; blocks, so callers run it off the UI thread."""
        state = load_state(self.install_dir) or InstallState(app_version=self.app_version, components=self.components)
        state.app_version, state.components = self.app_version, list(self.components)
        pending = pending_steps(self.steps, state, self.install_dir)
        for index, step in enumerate(pending):
            self._current = step.id
            if self._cancel.is_set():
                raise StepFailed("cancelled", "Installation cancelled", step.id)
            self._on_progress(step.id, step.title, index / len(pending))
            for action in step.actions:
                self._act(step, action)
            if not step.done_check(self.install_dir):
                raise StepFailed("command_failed", f"{step.title} did not finish", step.id)
            state.steps[step.id] = StepRecord(fingerprint=step.fingerprint, finished_at=datetime.now(UTC))
            save_state(self.install_dir, state)
        self._on_progress("", "", 1.0)

    def _act(self, step: Step, action) -> None:
        try:
            if isinstance(action, Command):
                self._run_command(action)
            else:
                action(StepContext(self.install_dir, self._log, self._cancel.is_set, self._run_command))
        except StepFailed as error:
            if self._cancel.is_set() and error.code != "cancelled":
                raise StepFailed("cancelled", "Installation cancelled", step.id) from error
            if not error.step_id:  # raised by an action, which does not know its step
                raise StepFailed(error.code, error.message, step.id) from error
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise StepFailed("network", str(error), step.id) from error
        except OSError as error:
            code = "no_space" if error.errno == errno.ENOSPC else "command_failed"
            raise StepFailed(code, str(error), step.id) from error

    def _run_command(self, command: Command) -> None:
        if self._cancel.is_set():
            raise StepFailed("cancelled", "Installation cancelled", self._current)
        self._log(f"$ {' '.join(command.argv)}")
        process = subprocess.Popen(
            command.argv, cwd=command.cwd, env={**os.environ, **command.env},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True,  # its own process group, so cancel stops its children too
        )
        with self._lock:
            self._process = process
        no_space = False
        try:
            for line in process.stdout:
                line = line.rstrip("\n")
                no_space = no_space or NO_SPACE in line
                self._log(line)
            code = process.wait()
        finally:
            with self._lock:
                self._process = None
        if self._cancel.is_set():
            raise StepFailed("cancelled", "Installation cancelled", self._current)
        if code != 0:
            raise StepFailed("no_space" if no_space else "command_failed",
                             f"{command.argv[0]} exited with {code}", self._current)

    def cancel(self) -> None:
        """Stop at once: terminate the running command's process group, then kill it if it lingers."""
        self._cancel.set()
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        _signal_group(process, signal.SIGTERM)

        def kill() -> None:
            if process.poll() is None:
                _signal_group(process, signal.SIGKILL)

        timer = threading.Timer(KILL_AFTER_S, kill)
        timer.daemon = True
        timer.start()

    def _log(self, line: str) -> None:
        self._on_log(line)
        path = self.install_dir / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(line + "\n")


def _signal_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
