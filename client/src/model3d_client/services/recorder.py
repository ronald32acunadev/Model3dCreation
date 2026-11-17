"""Records the default microphone to a WAV file.

Recording runs through QMediaCaptureSession and QMediaRecorder. The level meter opens a second QAudioSource on the
same input device and takes the RMS of the PCM it pulls. QAudioBufferOutput is not used: in Qt 6 it attaches to
QMediaPlayer, not to a capture session. A device whose sample format the meter cannot read still records, with no level.
"""

import array
import math
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioDevice,
    QAudioFormat,
    QAudioInput,
    QAudioSource,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaRecorder,
)

# Sample format -> (array typecode, full-scale value used to normalise samples to -1..1).
_SAMPLE_CODES = {QAudioFormat.SampleFormat.Int16: ("h", 32768.0), QAudioFormat.SampleFormat.Float: ("f", 1.0)}
_METER_GAIN = 4


def default_input() -> QAudioDevice:
    return QMediaDevices.defaultAudioInput()


def rms_level(data: bytes, sample_format: QAudioFormat.SampleFormat) -> float | None:
    """The loudness of raw PCM from 0 to 1, or None for a sample format the meter does not read."""
    if sample_format not in _SAMPLE_CODES:
        return None
    code, scale = _SAMPLE_CODES[sample_format]
    samples = array.array(code)
    samples.frombytes(data[: len(data) // samples.itemsize * samples.itemsize])
    if not samples:
        return None
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return min(1.0, math.sqrt(mean_square) / scale * _METER_GAIN)


def _meter_format(device: QAudioDevice) -> QAudioFormat | None:
    """The device's preferred format when the meter can read it, else mono 16 kHz Int16, else None."""
    preferred = device.preferredFormat()
    if preferred.sampleFormat() in _SAMPLE_CODES and device.isFormatSupported(preferred):
        return preferred
    fallback = QAudioFormat()
    fallback.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    fallback.setSampleRate(16000)
    fallback.setChannelCount(1)
    return fallback if device.isFormatSupported(fallback) else None


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
        self._level_source: QAudioSource | None = None
        self._level_io: QIODevice | None = None
        self._level_format: QAudioFormat | None = None
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
        self._start_level(device)
        self._recorder.record()
        self._limit.start(self.max_seconds * 1000)

    def stop(self) -> None:
        self._limit.stop()
        self._stop_level()
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

    def _start_level(self, device: QAudioDevice) -> None:
        self._stop_level()
        self._level_format = _meter_format(device)
        if self._level_format is None:
            return
        self._level_source = QAudioSource(device, self._level_format, self)
        self._level_io = self._level_source.start()
        if self._level_io is None:
            self._stop_level()
            return
        self._level_io.readyRead.connect(self._on_level_ready)

    def _stop_level(self) -> None:
        if self._level_source is not None:
            self._level_source.stop()
            self._level_source.deleteLater()
        self._level_source = None
        self._level_io = None

    def _on_level_ready(self) -> None:
        if self._level_io is not None:
            self._handle_level_data(bytes(self._level_io.readAll()))

    def _handle_level_data(self, data: bytes) -> None:
        if self._level_format is None:
            return
        value = rms_level(data, self._level_format.sampleFormat())
        if value is not None:
            self.level.emit(value)

    def _on_duration(self, milliseconds: int) -> None:
        self.elapsed.emit(milliseconds / 1000)

    def _on_state(self, state: QMediaRecorder.RecorderState) -> None:
        if state == QMediaRecorder.RecorderState.StoppedState:
            self._stop_level()
            if self._target is not None:
                target, self._target = self._target, None
                self._limit.stop()
                self.finished.emit(target)

    def _on_error(self, _error: QMediaRecorder.Error, message: str) -> None:
        self._limit.stop()
        self._stop_level()
        self._target = None
        self.failed.emit(message or "The recording failed")
