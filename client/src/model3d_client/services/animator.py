"""Detects a rigged model's body type and writes a walk animation, both in the engine's Python as a QProcess."""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

SCRIPT = Path(__file__).with_name("animate_rig.py")
BODIES = ("biped", "quadruped")
RESULT_TAG = "MODEL3D_RESULT "
ERROR_LINES = 5


class RigAnimator(QObject):
    detected = Signal(dict)  # the detection result: body, legs, arms, up
    walk_finished = Signal(object)  # the target Path
    failed = Signal(str)  # the reason

    def __init__(self, python: Path, script: Path = SCRIPT, parent: QObject | None = None):
        super().__init__(parent)
        self._python = python
        self._script = script
        self._process: QProcess | None = None
        self._mode: str | None = None
        self._target: Path | None = None

    @property
    def busy(self) -> bool:
        return self._process is not None

    def detect(self, source: Path) -> None:
        self._start("detect", [source], None)

    def walk(self, source: Path, target: Path, body: str) -> None:
        if body not in BODIES:
            raise ValueError(f"Unsupported body type: {body}")
        self._start("walk", [source, target, body], target)

    def _start(self, mode: str, args: list, target: Path | None) -> None:
        if self.busy:
            raise RuntimeError("An animation is already running")
        if not self._python.exists():
            self.failed.emit(f"The engine environment is missing: {self._python}")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CUDA_VISIBLE_DEVICES", "")  # animation never needs a GPU
        process = QProcess(self)
        process.setProcessEnvironment(env)
        process.setProgram(str(self._python))
        process.setArguments([str(self._script), mode, *[str(arg) for arg in args]])
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        self._mode = mode
        self._target = target
        process.start()

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        process, mode, target = self._process, self._mode, self._target
        stdout = bytes(process.readAllStandardOutput().data()).decode(errors="replace")
        stderr = bytes(process.readAllStandardError().data()).decode(errors="replace")
        self._settle()
        result = _parse_result(stdout)
        succeeded = status == QProcess.ExitStatus.NormalExit and code == 0 and result is not None
        if mode == "detect":
            if succeeded:
                self.detected.emit(result)
            else:
                self.failed.emit(_reason(stderr, code, status))
            return
        if succeeded and target.is_file() and target.stat().st_size > 0:
            self.walk_finished.emit(target)
            return
        _remove(target)
        self.failed.emit(_reason(stderr, code, status))

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart or self._process is None:
            return
        process, target = self._process, self._target
        reason = process.errorString()
        self._settle()
        _remove(target)
        self.failed.emit(reason)

    def _settle(self) -> None:
        """Forget the finished process before its signal is emitted, so a new start is allowed."""
        process, self._process, self._mode, self._target = self._process, None, None, None
        if process is not None:
            process.deleteLater()


def _parse_result(stdout: str) -> dict | None:
    """The dict on the last tagged line of the script's output; other lines are bpy noise."""
    result = None
    for line in stdout.splitlines():
        if line.startswith(RESULT_TAG):
            try:
                parsed = json.loads(line[len(RESULT_TAG):])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                result = parsed
    return result


def _reason(stderr: str, code: int, status: QProcess.ExitStatus) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()][-ERROR_LINES:]
    if lines:
        return "\n".join(lines)
    if status == QProcess.ExitStatus.NormalExit and code == 0:
        return "The script gave no result"
    return f"Animation failed with exit code {code}"


def _remove(target: Path | None) -> None:
    if target is not None and target.exists():
        target.unlink()
