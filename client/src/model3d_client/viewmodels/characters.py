"""One open character: its voice, the phrases it said, and saying a new one."""

import logging
from collections.abc import Callable
from pathlib import Path

from model3d_contracts.characters import Character, Characters, Phrase
from model3d_contracts.events import CancelledEvent, CompletedEvent, FailedEvent, ProgressEvent, SnapshotEvent
from model3d_contracts.jobs import Job, JobError, JobState
from model3d_contracts.voices import Voice
from PySide6.QtCore import QObject, Signal

from model3d_client.services.engine_api import ApiError, EngineApi, EventStream
from model3d_client.services.library import Library

logger = logging.getLogger(__name__)
LANGUAGES = ("es", "en")


def export_character(api: EngineApi, character_id: str, target: Path, on_done: Callable[[Path | ApiError], None]) -> None:
    """Write the character's .m3dchar package to `target`."""
    api.export_character(character_id, target, on_done)


def import_character(api: EngineApi, package: Path, on_done: Callable[[Character | ApiError], None]) -> None:
    """Send a .m3dchar package to the engine, which adds the character to the library."""
    api.import_character(package, on_done)


class CharacterViewModel(QObject):
    changed = Signal()
    phrases_changed = Signal()

    def __init__(self, api: EngineApi, library: Library, character_id: str, player, cache_dir: Path,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._api = api
        self._library = library
        self._player = player
        self._cache_dir = cache_dir
        self.character_id = character_id
        self.character: Character | None = Characters(library).get(character_id)
        self.voices: list[Voice] = []
        self.phrases: list[Phrase] = []
        self.busy = False
        self.progress = 0.0
        self.message = ""
        self.error: JobError | None = None
        self._job_id: str | None = None
        self._stream: EventStream | None = None
        self._reconnected = False

    @property
    def name(self) -> str:
        return self.character.name if self.character is not None else self.character_id

    @property
    def voice_id(self) -> str | None:
        return self.character.voice_id if self.character is not None else None

    def refresh(self) -> None:
        """Read the voices and the phrases from the engine."""
        self._api.voices(self._on_voices)
        self.refresh_phrases()

    def refresh_phrases(self, on_done: Callable[[], None] | None = None) -> None:
        self._api.phrases(self.character_id, lambda result: self._on_phrases(result, on_done))

    def set_voice(self, voice_id: str | None) -> None:
        if voice_id == self.voice_id:
            return
        self.error = None
        self._api.set_character_voice(self.character_id, voice_id, lambda result: self._on_voice_set(voice_id, result))

    def say(self, text: str, language: str) -> None:
        if self.busy or not text.strip() or self.voice_id is None:
            return
        self.busy = True
        self.progress = 0.0
        self.message = ""
        self.error = None
        self._reconnected = False
        self.changed.emit()
        self._api.character_speak(self.character_id, text, language, self._on_submitted)

    def play_phrase(self, phrase_id: str) -> None:
        self.error = None
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / f"{self.character_id}-{phrase_id}.wav"
        self._api.download_phrase(self.character_id, phrase_id, target, self._on_phrase_downloaded)

    def delete_phrase(self, phrase_id: str) -> None:
        self.error = None
        self._api.delete_phrase(self.character_id, phrase_id, lambda result: self._on_phrase_deleted(phrase_id, result))

    def _fail(self, error: JobError) -> None:
        self.busy = False
        self.error = error
        self.changed.emit()

    def _fail_api(self, result: ApiError) -> None:
        if result.code == "voice_not_assigned":
            self._fail(JobError(code=result.code, message="Pick a voice for this character first"))
        else:
            self._fail(JobError(code=result.code, message=result.message))

    def _on_voices(self, result: list[Voice] | ApiError) -> None:
        if isinstance(result, ApiError):
            self._fail_api(result)
            return
        self.voices = result
        self.changed.emit()

    def _on_phrases(self, result: list[Phrase] | ApiError, on_done: Callable[[], None] | None) -> None:
        if isinstance(result, ApiError):
            self._fail_api(result)
            return
        self.phrases = result
        self.phrases_changed.emit()
        if on_done is not None:
            on_done()

    def _on_voice_set(self, voice_id: str | None, result: ApiError | None) -> None:
        if isinstance(result, ApiError):
            self._fail_api(result)
            return
        if self.character is not None:
            self.character = self.character.model_copy(update={"voice_id": voice_id})
        self.changed.emit()

    def _on_submitted(self, result: Job | ApiError) -> None:
        if isinstance(result, ApiError):
            self._fail_api(result)
            return
        self._job_id = result.job_id
        self._open_stream()

    def _open_stream(self) -> None:
        self._stream = self._api.open_events(self._job_id)
        self._stream.event.connect(self._on_event)
        self._stream.closed.connect(self._on_closed)

    def _on_event(self, event) -> None:
        if not self.busy:
            return
        if isinstance(event, (SnapshotEvent, ProgressEvent)):
            self.progress = event.progress
            self.message = getattr(event, "message", "")
            self.changed.emit()
        elif isinstance(event, CompletedEvent):
            self._speech_done()
        elif isinstance(event, FailedEvent):
            self._fail(event.error)
        elif isinstance(event, CancelledEvent):
            self._fail(JobError(code="job_cancelled", message="The speech was cancelled"))

    def _on_closed(self) -> None:
        if self.busy and self._job_id is not None:
            self._api.job(self._job_id, self._on_job_after_close)

    def _on_job_after_close(self, result: Job | ApiError) -> None:
        if not self.busy:
            return
        if isinstance(result, ApiError):
            self._fail(JobError(code="engine_lost", message=f"The engine stopped answering: {result.message}"))
        elif result.state == JobState.succeeded:
            self._speech_done()
        elif result.state == JobState.failed:
            self._fail(result.error)
        elif result.state == JobState.cancelled:
            self._fail(JobError(code="job_cancelled", message="The speech was cancelled"))
        elif not self._reconnected:
            self._reconnected = True
            self._open_stream()
        else:
            self._fail(JobError(code="engine_lost", message="The engine stopped sending progress"))

    def _speech_done(self) -> None:
        self.progress = 1.0
        self.refresh_phrases(self._play_newest)

    def _play_newest(self) -> None:
        self.busy = False
        self.changed.emit()
        if self.phrases:
            self.play_phrase(self.phrases[0].phrase_id)

    def _on_phrase_downloaded(self, result: Path | ApiError) -> None:
        if isinstance(result, ApiError):
            self._fail_api(result)
            return
        self._player.play(result)

    def _on_phrase_deleted(self, phrase_id: str, result: ApiError | None) -> None:
        if isinstance(result, ApiError):
            self._fail_api(result)
            return
        (self._cache_dir / f"{self.character_id}-{phrase_id}.wav").unlink(missing_ok=True)
        self.refresh_phrases()
