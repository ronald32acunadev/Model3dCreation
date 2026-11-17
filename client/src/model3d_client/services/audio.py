"""Plays a local audio file through the default output."""

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class AudioPlayer(QObject):
    started = Signal()
    finished = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._output)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._active = False

    @property
    def playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self, path: Path) -> None:
        if not path.is_file():
            self.failed.emit(f"Audio file not found: {path}")
            return
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._active = True
        self._player.play()

    def stop(self) -> None:
        self._player.stop()

    def _on_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.started.emit()
        elif self._active:
            self._active = False
            self.finished.emit()

    def _on_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        self._active = False
        self.failed.emit(message or "The audio could not be played")
