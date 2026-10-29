"""Converts a library GLB to another format in the engine's Python, as a QProcess."""

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

FORMATS = {
    ".glb": "glTF binary",
    ".fbx": "FBX",
    ".obj": "Wavefront OBJ",
    ".stl": "STL",
    ".usdz": "USDZ",
}
CONVERTED = frozenset({".fbx", ".obj", ".stl", ".usdz"})
KEEPS_SKELETON = frozenset({".glb", ".fbx", ".usdz"})
KEEPS_TEXTURE = frozenset({".glb", ".fbx", ".obj", ".usdz"})
SCRIPT = Path(__file__).with_name("convert_model.py")
ERROR_LINES = 5


class ModelConverter(QObject):
    finished = Signal(object)  # the target Path
    failed = Signal(str)  # the reason

    def __init__(self, python: Path, script: Path = SCRIPT, parent: QObject | None = None):
        super().__init__(parent)
        self._python = python
        self._script = script
        self._process: QProcess | None = None
        self._target: Path | None = None

    @property
    def busy(self) -> bool:
        return self._process is not None

    def start(self, source: Path, target: Path) -> None:
        suffix = target.suffix.lower()
        if suffix not in CONVERTED:
            raise ValueError(f"Unsupported format: {suffix}")
        if self.busy:
            raise RuntimeError("A conversion is already running")
        if not self._python.exists():
            self.failed.emit(f"The engine environment is missing: {self._python}")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CUDA_VISIBLE_DEVICES", "")  # conversion never needs a GPU
        process = QProcess(self)
        process.setProcessEnvironment(env)
        process.setProgram(str(self._python))
        process.setArguments([str(self._script), str(source), str(target)])
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        self._target = target
        process.start()

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        process, target = self._process, self._target
        stderr = bytes(process.readAllStandardError().data()).decode(errors="replace")
        succeeded = (
            status == QProcess.ExitStatus.NormalExit
            and code == 0
            and target.is_file()
            and target.stat().st_size > 0
        )
        if succeeded:
            self._settle()
            self.finished.emit(target)
            return
        lines = [line.strip() for line in stderr.splitlines() if line.strip()][-ERROR_LINES:]
        reason = "\n".join(lines) or f"Conversion failed with exit code {code}"
        self._remove_outputs(target)
        self._settle()
        self.failed.emit(reason)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart or self._process is None:
            return
        process, target = self._process, self._target
        reason = process.errorString()
        self._remove_outputs(target)
        self._settle()
        self.failed.emit(reason)

    def _settle(self) -> None:
        """Forget the finished process before its signal is emitted, so a new start is allowed."""
        process, self._process, self._target = self._process, None, None
        if process is not None:
            process.deleteLater()

    @staticmethod
    def _remove_outputs(target: Path) -> None:
        if target.exists():
            target.unlink()
        if target.suffix.lower() == ".obj":
            material = target.with_suffix(".mtl")
            if material.exists():
                material.unlink()
