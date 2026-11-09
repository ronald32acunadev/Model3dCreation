"""The generation panel's state: from an input image to a finished library entry."""

import logging
import os
import secrets
import shutil
from enum import Enum
from pathlib import Path

from model3d_contracts.events import CancelledEvent, CompletedEvent, FailedEvent, ProgressEvent, SnapshotEvent
from model3d_contracts.health import Health
from model3d_contracts.jobs import Job, JobError, JobOptions, JobState, Quality
from PySide6.QtCore import QObject, Signal

from model3d_client.services.engine_api import ApiError, EngineApi, EventStream
from model3d_client.services.head_pass import HeadPass
from model3d_client.services.library import Entry, Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState, EngineSupervisor

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # the engine's limit (docs/api.md)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
ORIGINAL_MODEL = "model_original.glb"  # the job's own model, kept when the head pass replaces it
# The head pass's steps in order, as scripts/kit_assembly/run.py names them, for the progress bar.
DETAIL_STEPS = (
    "read the skeleton", "crop the parts", "sculpt the head", "align the head",
    "cut the head and blend the neck", "paint each piece", "render the comparison", "add the skeleton",
)


class Phase(str, Enum):
    idle = "idle"
    submitting = "submitting"
    queued = "queued"
    shaping = "shaping"
    painting = "painting"
    rigging = "rigging"
    detailing = "detailing"  # the head pass sculpts the face after the job finished
    cancelling = "cancelling"


class GenerationViewModel(QObject):
    changed = Signal()
    finished = Signal(object)  # the final Entry
    library_changed = Signal()

    def __init__(self, api: EngineApi, library: Library, supervisor: EngineSupervisor, settings: Settings,
                 head_pass: HeadPass | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._api = api
        self._library = library
        self._supervisor = supervisor
        self._head_pass = head_pass
        if head_pass is not None:
            head_pass.step.connect(self._on_detail_step)
            head_pass.finished.connect(self._on_detail_finished)
            head_pass.skipped.connect(self._on_detail_skipped)
            head_pass.failed.connect(self._on_detail_failed)
        self.image: Path | None = None
        self.image_problem: str | None = None
        self.texture = settings.default_texture
        self.quality = settings.default_quality
        self.rig = False
        self.detailed_face = False
        self.notice: str | None = None  # why the last detailed face was not applied
        self._detail = False  # the running generation ends with a head pass
        self._detailing = False  # the head pass is running
        self._detail_stats = None
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
    def rig_available(self) -> bool:
        health: Health | None = self._supervisor.health
        return health.rig if health is not None else False

    @property
    def rig_reason(self) -> str | None:
        health: Health | None = self._supervisor.health
        return health.rig_reason if health is not None else None

    @property
    def detail_reason(self) -> str | None:
        """Why the detailed face is unavailable ('missing_tools', 'no_rig', 'not_trellis'), or None."""
        if self._head_pass is None or not self._head_pass.installed:
            return "missing_tools"
        if not self.rig_available:
            return "no_rig"
        health: Health | None = self._supervisor.health
        if health is None or "TRELLIS.2" not in health.mode.reason:
            return "not_trellis"
        return None

    def can_add_skeleton(self, entry: Entry) -> bool:
        return (
            self._supervisor.state == EngineState.ready
            and self.phase == Phase.idle
            and self.rig_available
            and entry.outcome == Outcome.succeeded
            and not self._library.has_skeleton(entry)
        )

    def can_regenerate(self, entry: Entry) -> bool:
        return (
            self._supervisor.state == EngineState.ready
            and self.phase == Phase.idle
            and entry.outcome == Outcome.succeeded
        )

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
        return self.phase in (Phase.submitting, Phase.queued, Phase.shaping, Phase.painting, Phase.rigging,
                              Phase.detailing)

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

    def set_rig(self, rig: bool) -> None:
        self.rig = rig
        self.changed.emit()

    def set_quality(self, quality: Quality) -> None:
        self.quality = quality
        self.changed.emit()

    def set_detailed_face(self, detailed: bool) -> None:
        self.detailed_face = detailed
        self.changed.emit()

    def generate(self) -> None:
        if not self.can_generate:
            return
        detail = self.detailed_face and self.detail_reason is None
        options = JobOptions(
            texture=self.texture and self.texture_available, quality=self.quality,
            # The head pass finds the head through the skeleton, and returns a rigged model.
            rig=(self.rig or detail) and self.rig_available,
        )
        self.entry = self._library.create(self.image, options)
        self._start()
        self._detail = detail
        self._api.submit(self.image, options, self._on_submitted)

    def add_skeleton(self, entry_id: str) -> None:
        """Rig an existing entry's model into a new entry; the original stays as it is."""
        source = next((entry for entry in self._library.entries() if entry.entry_id == entry_id), None)
        if source is None or not self.can_add_skeleton(source):
            return
        self.entry = self._library.create_derived(entry_id)
        self._start()
        self._api.submit_rig(self._library.model_path(entry_id), self._on_submitted)

    def regenerate(self, entry_id: str) -> None:
        """Generate the entry's image again with its options and a different seed, into a new entry."""
        source = next((entry for entry in self._library.entries() if entry.entry_id == entry_id), None)
        if source is None or not self.can_regenerate(source):
            return
        seed = secrets.randbelow(2**32)
        while seed == source.seed:
            seed = secrets.randbelow(2**32)
        options = source.options.model_copy(update={
            "seed": seed,
            "texture": source.options.texture and self.texture_available,
            "rig": source.options.rig and self.rig_available,
        })
        image = self._library.input_path(entry_id)
        self.entry = self._library.create(image, options, name=source.name)
        self._start()
        self._api.submit(image, options, self._on_submitted)

    def _start(self) -> None:
        self.library_changed.emit()
        self.error = None
        self.notice = None
        self._detail = False
        self._detailing = False
        self._cancel_pending = False
        self._reconnected = False
        self._set_phase(Phase.submitting, 0.0, "")

    def cancel(self) -> None:
        if not self.can_cancel:
            return
        if self.phase == Phase.detailing:
            self._head_pass.cancel()  # the job's own model stays in the library
        elif self.phase == Phase.submitting:
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
            phase = {"shape": Phase.shaping, "paint": Phase.painting, "rig": Phase.rigging}.get(event.stage, Phase.queued)
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
        # Once the head pass runs, the job is over and its socket closing means nothing.
        if self.entry is not None and not self._downloading and not self._detailing:
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
            elif self._detail and self.phase != Phase.cancelling:
                self._start_detail(stats)
            else:
                self._finish(Outcome.succeeded, stats=stats)

        self._api.download_result(self.entry.job_id, target, done)

    def _start_detail(self, stats) -> None:
        """The job's model is in the library; sculpt the face and swap it in when that works."""
        self._detail_stats = stats
        self._detailing = True
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._set_phase(Phase.detailing, 0.0, "")
        self._head_pass.start(self.entry.job_id)

    def _on_detail_step(self, step: str) -> None:
        if not self._detailing or self.phase == Phase.cancelling:
            return
        done = DETAIL_STEPS.index(step) / len(DETAIL_STEPS) if step in DETAIL_STEPS else self.progress
        self._set_phase(Phase.detailing, done, step)

    def _on_detail_finished(self, model: Path) -> None:
        if not self._detailing:
            return
        target = self._library.model_path(self.entry.entry_id)
        try:
            shutil.copyfile(target, target.with_name(ORIGINAL_MODEL))
            part = target.with_suffix(".part")
            shutil.copyfile(model, part)
            os.replace(part, target)
        except OSError as error:
            self._end_detail(f"Could not save the detailed model: {error}")
            return
        self._end_detail(None)

    def _on_detail_skipped(self, reason: str) -> None:
        if self._detailing:
            self._end_detail(reason)

    def _on_detail_failed(self, reason: str) -> None:
        if self._detailing:
            logger.warning("Head pass failed: %s", reason)
            self._end_detail(reason)

    def _end_detail(self, notice: str | None) -> None:
        """The entry succeeds either way: with the head-pass model, or with the job's own model and a notice."""
        self._detailing = False
        self._finish(Outcome.succeeded, stats=self._detail_stats)
        self.notice = notice
        self.changed.emit()

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
