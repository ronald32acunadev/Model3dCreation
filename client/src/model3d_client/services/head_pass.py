"""Runs the head pass (scripts/kit_assembly/run.py) on a finished job, as a QProcess.

The script sculpts the head on its own, swaps it into the job's model, and rigs the result again
through the engine. It prints "[run]  <step>" lines while it works and one MODEL3D_HEAD_PASS line
with the result at the end.
"""

import json
import os
import signal
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

RESULT_TAG = "MODEL3D_HEAD_PASS "
STEP_TAG = "[run]  "
ERROR_LINES = 5
SCRIPT = Path("scripts/kit_assembly/run.py")  # relative to the repository or install folder


class HeadPass(QObject):
    step = Signal(str)  # the step now running, as the script names it
    finished = Signal(object)  # Path of the head-pass model
    skipped = Signal(str)  # why the model was left as it is
    failed = Signal(str)  # the reason

    def __init__(self, python: Path, script: Path, home: Path, engine_url: str = "http://127.0.0.1:8765",
                 parent: QObject | None = None):
        super().__init__(parent)
        self._python = python
        self._script = script
        self._home = home
        self._engine_url = engine_url
        self._process: QProcess | None = None
        self._buffer = ""
        self._result: dict | None = None
        self._output: list[str] = []
        self._cancelled = False

    @property
    def installed(self) -> bool:
        return self._script.is_file() and self._python.exists()

    @property
    def busy(self) -> bool:
        return self._process is not None

    def start(self, job_id: str) -> None:
        if self.busy:
            raise RuntimeError("A head pass is already running")
        if not self.installed:
            self.failed.emit(f"The head-pass tools are missing: {self._script}")
            return
        process = QProcess(self)
        process.setProgram(str(self._python))
        process.setArguments([str(self._script), job_id, "--home", str(self._home), "--engine", self._engine_url])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        self._buffer, self._result, self._output, self._cancelled = "", None, [], False
        process.start()

    def cancel(self) -> None:
        """Stop the script; it stops its own steps and any engine it started."""
        if self._process is None:
            return
        self._cancelled = True
        pid = self._process.processId()
        if pid:
            os.kill(pid, signal.SIGTERM)

    def _on_output(self) -> None:
        self._buffer += bytes(self._process.readAllStandardOutput().data()).decode(errors="replace")
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._read_line(line)

    def _read_line(self, line: str) -> None:
        if line.startswith(RESULT_TAG):
            try:
                self._result = json.loads(line[len(RESULT_TAG):])
            except ValueError:
                pass
        elif line.startswith(STEP_TAG):
            self.step.emit(line[len(STEP_TAG):].strip())
        if line.strip():
            self._output.append(line.strip())

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        self._read_line(self._buffer)
        result, output, cancelled = self._result, self._output, self._cancelled
        self._settle()
        if cancelled:
            self.skipped.emit("cancelled")
        elif status == QProcess.ExitStatus.NormalExit and code == 0 and result is not None:
            if result.get("applied"):
                self.finished.emit(Path(result["model"]))
            else:
                self.skipped.emit(result.get("reason") or "no usable head")
        else:
            self.failed.emit("\n".join(output[-ERROR_LINES:]) or f"The head pass failed with exit code {code}")

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart or self._process is None:
            return
        reason = self._process.errorString()
        self._settle()
        self.failed.emit(reason)

    def _settle(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.deleteLater()
