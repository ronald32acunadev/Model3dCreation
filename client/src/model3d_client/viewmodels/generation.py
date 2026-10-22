"""The generation panel's state: from an input image to a finished library entry."""

import logging
from enum import Enum
from pathlib import Path

from model3d_contracts.events import CancelledEvent, CompletedEvent, FailedEvent, ProgressEvent, SnapshotEvent
from model3d_contracts.health import Health
from model3d_contracts.jobs import Job, JobError, JobOptions, JobState, Quality
from PySide6.QtCore import QObject, Signal

from model3d_client.services.engine_api import ApiError, EngineApi, EventStream
from model3d_client.services.library import Entry, Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState, EngineSupervisor

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # the engine's limit (docs/api.md)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


class Phase(str, Enum):
    idle = "idle"
    submitting = "submitting"
    queued = "queued"
    shaping = "shaping"
    painting = "painting"
    cancelling = "cancelling"


class GenerationViewModel(QObject):
    changed = Signal()
    finished = Signal(object)  # the final Entry
    library_changed = Signal()

    def __init__(self, api: EngineApi, library: Library, supervisor: EngineSupervisor, settings: Settings,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._api = api
        self._library = library
        self._supervisor = supervisor
        self.image: Path | None = None
        self.image_problem: str | None = None
        self.texture = settings.default_texture
        self.quality = settings.default_quality
        self.phase = Phase.idle
        self.progress = 0.0
        self.message = ""
        self.error: JobError | None = None
        self.entry: Entry | None = None  # the entry of the running generation
        self._stream: EventStream | None = None
        self._cancel_pending = False
        self._reconnected = False
        self._downloading = False
        supervisor.state_changed.connect(lambda _state: self.changed.emit())
        supervisor.health_changed.connect(lambda _health: self.changed.emit())

    @property
    def texture_available(self) -> bool:
        health: Health | None = self._supervisor.health
        return health.mode.texture if health is not None else True

    @property
    def can_generate(self) -> bool:
        return (
            self._supervisor.state == EngineState.ready
            and self.image is not None
            and self.image_problem is None
            and self.phase == Phase.idle
        )

    @property
    def can_cancel(self) -> bool:
        return self.phase in (Phase.submitting, Phase.queued, Phase.shaping, Phase.painting)

    def set_image(self, path: Path) -> None:
        self.image = path
        if not path.is_file():
            self.image_problem = "missing"
        elif path.suffix.lower() not in IMAGE_SUFFIXES:
            self.image_problem = "unsupported_format"
        elif path.stat().st_size > MAX_IMAGE_BYTES:
            self.image_problem = "too_large"
        else:
            self.image_problem = None
        self.changed.emit()

    def set_texture(self, texture: bool) -> None:
        self.texture = texture
        self.changed.emit()

    def set_quality(self, quality: Quality) -> None:
        self.quality = quality
        self.changed.emit()

    def generate(self) -> None:
        if not self.can_generate:
            return
        options = JobOptions(texture=self.texture and self.texture_available, quality=self.quality)
        self.entry = self._library.create(self.image, options)
        self.library_changed.emit()
        self.error = None
        self._cancel_pending = False
        self._reconnected = False
        self._set_phase(Phase.submitting, 0.0, "")
        self._api.submit(self.image, options, self._on_submitted)

    def cancel(self) -> None:
        if not self.can_cancel:
            return
        if self.phase == Phase.submitting:
            self._cancel_pending = True  # sent once the engine returns the job id
        else:
            self._api.cancel(self.entry.job_id, self._on_cancel_answer)
        self._set_phase(Phase.cancelling, self.progress, self.message)

    def _on_submitted(self, result: Job | ApiError) -> None:
        if isinstance(result, ApiError):
            self._finish(Outcome.failed, JobError(code=result.code, message=result.message))
            return
        self.entry = self.entry.model_copy(update={"job_id": result.job_id, "seed": result.seed})
        self._library.update(self.entry)
        if self._cancel_pending:
            self._api.cancel(result.job_id, self._on_cancel_answer)
        else:
            self._set_phase(Phase.queued, 0.0, "")
        self._open_stream()

    def _open_stream(self) -> None:
        self._stream = self._api.open_events(self.entry.job_id)
        self._stream.event.connect(self._on_event)
        self._stream.closed.connect(self._on_closed)

    def _on_cancel_answer(self, result: Job | ApiError) -> None:
        # job_finished: the final event is on its way; other errors surface through the socket.
        if isinstance(result, ApiError) and result.code != "job_finished":
            logger.warning("Cancel failed: %s %s", result.code, result.message)

    def _on_event(self, event) -> None:
        if self.entry is None:
            return
        if isinstance(event, (SnapshotEvent, ProgressEvent)):
            phase = {"shape": Phase.shaping, "paint": Phase.painting}.get(event.stage, Phase.queued)
            if self.phase == Phase.cancelling:
                phase = Phase.cancelling
            self._set_phase(phase, event.progress, getattr(event, "message", ""))
        elif isinstance(event, CompletedEvent):
            self._download(event.stats)
        elif isinstance(event, FailedEvent):
            self._finish(Outcome.failed, event.error)
        elif isinstance(event, CancelledEvent):
            self._finish(Outcome.cancelled)

    def _on_closed(self) -> None:
        # A socket that closes before a final event means the engine went away or dropped us.
        if self.entry is not None and not self._downloading:
            self._api.job(self.entry.job_id, self._on_job_after_close)

    def _on_job_after_close(self, result: Job | ApiError) -> None:
        if self.entry is None:
            return
        if isinstance(result, ApiError):
            message = f"The engine stopped answering: {result.message}"
            self._finish(Outcome.failed, JobError(code="engine_lost", message=message))
        elif result.state == JobState.succeeded:
            self._download(None)
        elif result.state == JobState.failed:
            self._finish(Outcome.failed, result.error)
        elif result.state == JobState.cancelled:
            self._finish(Outcome.cancelled)
        elif not self._reconnected:
            self._reconnected = True
            self._open_stream()
        else:
            self._finish(Outcome.failed, JobError(code="engine_lost", message="The engine stopped sending progress"))

    def _download(self, stats) -> None:
        self._downloading = True
        target = self._library.model_path(self.entry.entry_id)

        def done(result: Path | ApiError) -> None:
            self._downloading = False
            if isinstance(result, ApiError):
                self._finish(Outcome.failed, JobError(code=result.code, message=result.message))
            else:
                self._finish(Outcome.succeeded, stats=stats)

        self._api.download_result(self.entry.job_id, target, done)

    def _finish(self, outcome: Outcome, error: JobError | None = None, stats=None) -> None:
        if self.entry is None:
            return
        entry = self.entry.model_copy(update={"outcome": outcome, "error": error, "stats": stats})
        self._library.update(entry)
        self.entry = None
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self.error = error
        self._set_phase(Phase.idle, 0.0, "")
        self.library_changed.emit()
        self.finished.emit(entry)

    def _set_phase(self, phase: Phase, progress: float, message: str) -> None:
        self.phase, self.progress, self.message = phase, progress, message
        self.changed.emit()
