"""Records the default microphone to a WAV file.

The level meter uses QAudioBufferOutput (Qt 6.8+) to take the RMS of the captured buffers. Older Qt builds have
no such API, so they emit no level and only report elapsed time.
"""

import array
import math
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioDevice,
    QAudioFormat,
    QAudioInput,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaRecorder,
)

try:
    from PySide6.QtMultimedia import QAudioBufferOutput
except ImportError:
    QAudioBufferOutput = None


def default_input() -> QAudioDevice:
    return QMediaDevices.defaultAudioInput()


class Recorder(QObject):
    level = Signal(float)  # 0..1
    elapsed = Signal(float)  # seconds
    finished = Signal(object)  # the recorded Path
    failed = Signal(str)

    max_seconds = 30

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._session: QMediaCaptureSession | None = None
        self._recorder: QMediaRecorder | None = None
        self._buffers = None
        self._target: Path | None = None
        self._limit = QTimer(self)
        self._limit.setSingleShot(True)
        self._limit.timeout.connect(self.stop)

    @property
    def recording(self) -> bool:
        return self._recorder is not None and self._recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState

    def start(self, target: Path) -> None:
        device = default_input()
        if device.isNull():
            self.failed.emit("No microphone found")
            return
        self._build(device)
        self._target = target
        target.parent.mkdir(parents=True, exist_ok=True)
        self._recorder.setOutputLocation(QUrl.fromLocalFile(str(target)))
        self._recorder.record()
        self._limit.start(self.max_seconds * 1000)

    def stop(self) -> None:
        self._limit.stop()
        if self._recorder is not None:
            self._recorder.stop()

    def _build(self, device: QAudioDevice) -> None:
        self._session = QMediaCaptureSession(self)
        self._session.setAudioInput(QAudioInput(device, self))
        self._recorder = QMediaRecorder(self)
        self._recorder.setMediaFormat(QMediaFormat(QMediaFormat.FileFormat.Wave))
        self._recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        self._session.setRecorder(self._recorder)
        self._recorder.durationChanged.connect(self._on_duration)
        self._recorder.recorderStateChanged.connect(self._on_state)
        self._recorder.errorOccurred.connect(self._on_error)
        if QAudioBufferOutput is not None:
            self._buffers = QAudioBufferOutput(self)
            self._buffers.audioBufferReceived.connect(self._on_buffer)
            self._session.setAudioBufferOutput(self._buffers)

    def _on_duration(self, milliseconds: int) -> None:
        self.elapsed.emit(milliseconds / 1000)

    def _on_state(self, state: QMediaRecorder.RecorderState) -> None:
        if state == QMediaRecorder.RecorderState.StoppedState and self._target is not None:
            target, self._target = self._target, None
            self._limit.stop()
            self.finished.emit(target)

    def _on_error(self, _error: QMediaRecorder.Error, message: str) -> None:
        self._limit.stop()
        self._target = None
        self.failed.emit(message or "The recording failed")

    def _on_buffer(self, buffer: QAudioBuffer) -> None:
        value = _rms(buffer)
        if value is not None:
            self.level.emit(value)


def _rms(buffer: QAudioBuffer) -> float | None:
    """The buffer's loudness from 0 to 1, or None for a sample format this meter does not read."""
    sample_format = buffer.format().sampleFormat()
    codes = {QAudioFormat.SampleFormat.Int16: ("h", 32768.0), QAudioFormat.SampleFormat.Float: ("f", 1.0)}
    if sample_format not in codes:
        return None
    code, scale = codes[sample_format]
    samples = array.array(code)
    samples.frombytes(bytes(buffer.constData())[: buffer.byteCount() // samples.itemsize * samples.itemsize])
    if not samples:
        return None
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return min(1.0, math.sqrt(mean_square) / scale * 4)
