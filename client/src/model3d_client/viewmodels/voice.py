"""The Voice section's state: saved voices, voice design, recording and speech generation."""

import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from model3d_contracts.events import CancelledEvent, CompletedEvent, FailedEvent, ProgressEvent, SnapshotEvent
from model3d_contracts.jobs import Job, JobError, JobState
from model3d_contracts.voices import Voice
from PySide6.QtCore import QObject, Signal

from model3d_client.services.engine_api import ApiError, EngineApi, EventStream
from model3d_client.services.recorder import Recorder
from model3d_client.services.supervisor import EngineState, EngineSupervisor


class _JobFollower:
    """Follows one job over its event socket, falling back to the job record when the socket closes early."""

    def __init__(self, api: EngineApi, job_id: str, on_progress: Callable[[float, str], None],
                 on_completed: Callable[[], None], on_failed: Callable[[JobError], None]):
        self._api = api
        self._job_id = job_id
        self._on_progress = on_progress
        self._on_completed = on_completed
        self._on_failed = on_failed
        self._stream: EventStream | None = None
        self._reconnected = False
        self._done = False
        self._open()

    def stop(self) -> None:
        self._done = True
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def _open(self) -> None:
        self._stream = self._api.open_events(self._job_id)
        self._stream.event.connect(self._on_event)
        self._stream.closed.connect(self._on_closed)

    def _finish(self, callback: Callable, *args) -> None:
        if self._done:
            return
        self.stop()
        callback(*args)

    def _on_event(self, event) -> None:
        if isinstance(event, (SnapshotEvent, ProgressEvent)):
            if not self._done:
                self._on_progress(event.progress, getattr(event, "message", ""))
        elif isinstance(event, CompletedEvent):
            self._finish(self._on_completed)
        elif isinstance(event, FailedEvent):
            self._finish(self._on_failed, event.error)
        elif isinstance(event, CancelledEvent):
            self._finish(self._on_failed, JobError(code="cancelled", message="The job was cancelled"))

    def _on_closed(self) -> None:
        if not self._done:
            self._api.job(self._job_id, self._on_job)

    def _on_job(self, result: Job | ApiError) -> None:
        if self._done:
            return
        if isinstance(result, ApiError):
            self._finish(self._on_failed, JobError(code="engine_lost", message=f"The engine stopped answering: {result.message}"))
        elif result.state == JobState.succeeded:
            self._finish(self._on_completed)
        elif result.state == JobState.failed:
            self._finish(self._on_failed, result.error)
        elif result.state == JobState.cancelled:
            self._finish(self._on_failed, JobError(code="cancelled", message="The job was cancelled"))
        elif not self._reconnected:
            self._reconnected = True
            self._open()
        else:
            self._finish(self._on_failed, JobError(code="engine_lost", message="The engine stopped sending progress"))


class VoiceViewModel(QObject):
    changed = Signal()
    voices_changed = Signal()
    error_changed = Signal()

    def __init__(self, api: EngineApi, supervisor: EngineSupervisor, cache_dir: Path, player,
                 recorder_factory: Callable[[], object] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._api = api
        self._supervisor = supervisor
        self._cache = cache_dir
        self.player = player
        self._recorder_factory = recorder_factory or Recorder
        self._recorder = None
        self.voices: list[Voice] = []
        self.error: JobError | None = None
        self._loaded = False
        self._preview_follower: _JobFollower | None = None
        self._speech_follower: _JobFollower | None = None

        self.previewing = False
        self.preview_progress = 0.0
        self.preview_message = ""
        self.preview_id: str | None = None
        self.preview_path: Path | None = None

        self.recording = False
        self.level = 0.0
        self.elapsed = 0.0
        self.take: Path | None = None
        self.transcribing = False
        self.transcript: str | None = None
        self._takes = 0

        self.generating = False
        self.progress = 0.0
        self.message = ""
        self.last_clip: Path | None = None
        self.saving = False

        supervisor.state_changed.connect(self._on_supervisor)
        supervisor.health_changed.connect(self._on_supervisor)
        player.failed.connect(self._on_player_failed)

    @property
    def preview_ready(self) -> bool:
        return self.preview_id is not None

    @property
    def engine_ready(self) -> bool:
        return self._supervisor.state == EngineState.ready

    @property
    def available(self) -> bool:
        health = self._supervisor.health
        return self.engine_ready and health is not None and health.voice

    @property
    def unavailable_reason(self) -> str | None:
        """The engine's reason voice generation is off; None when it is on or the engine gave none."""
        health = self._supervisor.health
        return None if self.available or health is None else health.voice_reason

    @property
    def busy(self) -> bool:
        return self.previewing or self.generating or self.transcribing or self.saving

    def _on_supervisor(self, *_args) -> None:
        if self.available and not self._loaded:
            self.refresh()
        elif not self.available:
            self._loaded = False
        self.changed.emit()

    def _on_player_failed(self, message: str) -> None:
        self._set_error(JobError(code="playback_failed", message=message))

    def _set_error(self, error: JobError | None) -> None:
        self.error = error
        self.error_changed.emit()
        self.changed.emit()

    def _fail(self, result: ApiError) -> None:
        self._set_error(JobError(code=result.code, message=result.message))

    def _cache_file(self, *parts: str) -> Path:
        path = self._cache.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _guard(self) -> bool:
        return self.available

    def refresh(self) -> None:
        if not self._guard():
            return
        self._loaded = True
        self._api.voices(self._on_voices)

    def _on_voices(self, result: list[Voice] | ApiError) -> None:
        if isinstance(result, ApiError):
            self._loaded = False
            self._fail(result)
            return
        self.voices = result
        self.voices_changed.emit()
        self.changed.emit()

    def voice(self, voice_id: str) -> Voice | None:
        return next((voice for voice in self.voices if voice.voice_id == voice_id), None)

    def play_sample(self, voice_id: str) -> None:
        if not self._guard():
            return
        target = self._cache_file("samples", f"{voice_id}.wav")
        self._api.download_voice_sample(voice_id, target, self._on_downloaded_to_play)

    def _on_downloaded_to_play(self, result: Path | ApiError) -> None:
        if isinstance(result, ApiError):
            self._fail(result)
        else:
            self.player.play(result)

    def rename(self, voice_id: str, name: str) -> None:
        if self._guard():
            self._api.rename_voice(voice_id, name, self._on_changed_voice)

    def delete(self, voice_id: str) -> None:
        if self._guard():
            self._api.delete_voice(voice_id, self._on_deleted)

    def _on_changed_voice(self, result: Voice | ApiError) -> None:
        if isinstance(result, ApiError):
            self._fail(result)
        else:
            self.refresh()

    def _on_deleted(self, result: ApiError | None) -> None:
        if result is not None:
            self._fail(result)
        else:
            self.refresh()

    def preview(self, description: str, language: str, sample_text: str) -> None:
        if not self._guard() or self.previewing:
            return
        self.error = None
        self.preview_id = None
        self.preview_path = None
        self.previewing = True
        self.preview_progress, self.preview_message = 0.0, ""
        self.changed.emit()
        self._api.preview_voice(description, language, sample_text, self._on_preview_submitted)

    def _on_preview_submitted(self, result: Job | ApiError) -> None:
        if isinstance(result, ApiError):
            self.previewing = False
            self._fail(result)
            return
        job_id = result.job_id
        self._preview_follower = _JobFollower(
            self._api, job_id, self._on_preview_progress,
            lambda: self._download_preview(job_id), self._on_preview_failed,
        )

    def _on_preview_progress(self, progress: float, message: str) -> None:
        self.preview_progress, self.preview_message = progress, message
        self.changed.emit()

    def _on_preview_failed(self, error: JobError) -> None:
        self.previewing = False
        self._set_error(error)

    def _download_preview(self, job_id: str) -> None:
        target = self._cache_file("previews", f"{job_id}.wav")
        self._api.download_speech(job_id, target, lambda result: self._on_preview_downloaded(job_id, result))

    def _on_preview_downloaded(self, job_id: str, result: Path | ApiError) -> None:
        self.previewing = False
        if isinstance(result, ApiError):
            self._fail(result)
            return
        self.preview_id, self.preview_path = job_id, result
        self.changed.emit()
        self.player.play(result)

    def play_preview(self) -> None:
        if self.preview_path is not None:
            self.player.play(self.preview_path)

    def save_preview(self, name: str) -> None:
        if not self._guard() or self.preview_id is None or self.saving:
            return
        self.saving = True
        self.changed.emit()
        self._api.save_designed_voice(name, self.preview_id, self._on_preview_saved)

    def _on_preview_saved(self, result: Voice | ApiError) -> None:
        self.saving = False
        if isinstance(result, ApiError):
            self._fail(result)
            return
        self.preview_id, self.preview_path = None, None
        self.refresh()
        self.changed.emit()

    def start_recording(self) -> None:
        if not self._guard() or self.recording or self.transcribing:
            return
        if self._recorder is None:
            self._recorder = self._recorder_factory()
            self._recorder.level.connect(self._on_level)
            self._recorder.elapsed.connect(self._on_elapsed)
            self._recorder.finished.connect(self._on_recorded)
            self._recorder.failed.connect(self._on_recording_failed)
        self.player.stop()
        self.error = None
        self.transcript = None
        self.recording = True
        self.level, self.elapsed = 0.0, 0.0
        self._takes += 1
        self.changed.emit()
        self._recorder.start(self._cache / "takes" / f"take-{self._takes}.wav")

    def stop_recording(self) -> None:
        if self.recording and self._recorder is not None:
            self._recorder.stop()

    def _on_level(self, level: float) -> None:
        self.level = level
        self.changed.emit()

    def _on_elapsed(self, seconds: float) -> None:
        self.elapsed = seconds
        self.changed.emit()

    def _on_recorded(self, path: Path) -> None:
        self.recording = False
        self.level = 0.0
        self.take = path
        self.changed.emit()

    def _on_recording_failed(self, message: str) -> None:
        self.recording = False
        self.level = 0.0
        self._set_error(JobError(code="recording_failed", message=message))

    def play_take(self) -> None:
        if self.take is not None:
            self.player.play(self.take)

    def discard_take(self) -> None:
        if self.take is not None:
            self.take.unlink(missing_ok=True)
        self.take = None
        self.transcript = None
        self.changed.emit()

    def save_recording(self, name: str, language: str) -> None:
        if not self._guard() or self.take is None or self.transcribing:
            return
        self.transcribing = True
        self.error = None
        self.changed.emit()
        self._api.save_recorded_voice(name, language, self.take, self._on_recording_saved)

    def _on_recording_saved(self, result: Voice | ApiError) -> None:
        self.transcribing = False
        if isinstance(result, ApiError):
            self._fail(result)
            return
        self.transcript = result.transcript
        if self.take is not None:
            self.take.unlink(missing_ok=True)
        self.take = None
        self.refresh()
        self.changed.emit()

    def history(self, voice_id: str) -> list[Path]:
        """The clips generated with a voice, newest first; read from disk so they survive restarts."""
        folder = self._cache / "history" / voice_id
        return sorted(folder.glob("*.wav"), reverse=True) if folder.is_dir() else []

    def generate(self, voice_id: str, language: str, text: str) -> None:
        if not self._guard() or self.generating:
            return
        self.error = None
        self.generating = True
        self.progress, self.message = 0.0, ""
        self.changed.emit()
        self._api.speak(voice_id, language, text, lambda result: self._on_speech_submitted(voice_id, result))

    def _on_speech_submitted(self, voice_id: str, result: Job | ApiError) -> None:
        if isinstance(result, ApiError):
            self.generating = False
            self._fail(result)
            return
        job_id = result.job_id
        self._speech_follower = _JobFollower(
            self._api, job_id, self._on_speech_progress,
            lambda: self._download_clip(voice_id, job_id), self._on_speech_failed,
        )

    def _on_speech_progress(self, progress: float, message: str) -> None:
        self.progress, self.message = progress, message
        self.changed.emit()

    def _on_speech_failed(self, error: JobError) -> None:
        self.generating = False
        self._set_error(error)

    def _download_clip(self, voice_id: str, job_id: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self._cache_file("history", voice_id, f"{stamp}-{job_id}.wav")
        self._api.download_speech(job_id, target, self._on_clip_downloaded)

    def _on_clip_downloaded(self, result: Path | ApiError) -> None:
        self.generating = False
        if isinstance(result, ApiError):
            self._fail(result)
            return
        self.last_clip = result
        self.changed.emit()
        self.player.play(result)

    def play_clip(self, path: Path) -> None:
        self.player.play(path)

    def save_clip(self, target: Path, clip: Path | None = None) -> None:
        source = clip or self.last_clip
        if source is None:
            return
        shutil.copyfile(source, target)

    def delete_clip(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        if self.last_clip == path:
            self.last_clip = None
        self.changed.emit()
