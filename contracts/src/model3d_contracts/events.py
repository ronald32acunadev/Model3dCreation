"""Events streamed over a job's WebSocket."""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from .jobs import JobError, JobState, Stage, UtcDatetime


class ResultStats(BaseModel):
    """Statistics of a finished model."""

    vertices: int
    faces: int
    textured: bool
    duration_s: float


class _EventBase(BaseModel):
    job_id: str
    ts: UtcDatetime


class SnapshotEvent(_EventBase):
    """Current job state, sent first on every connection."""

    type: Literal["snapshot"] = "snapshot"
    state: JobState
    stage: Stage | None = None
    progress: float = 0.0


class ProgressEvent(_EventBase):
    """Progress within the current stage, 0.0 to 1.0."""

    type: Literal["progress"] = "progress"
    stage: Stage
    progress: float
    message: str = ""


class CompletedEvent(_EventBase):
    """The job succeeded; the result is ready to download."""

    type: Literal["completed"] = "completed"
    result_url: str
    stats: ResultStats


class FailedEvent(_EventBase):
    """The job failed."""

    type: Literal["failed"] = "failed"
    error: JobError


class CancelledEvent(_EventBase):
    """The job was cancelled."""

    type: Literal["cancelled"] = "cancelled"


Event = Annotated[
    Union[SnapshotEvent, ProgressEvent, CompletedEvent, FailedEvent, CancelledEvent],
    Field(discriminator="type"),
]

FINAL_EVENT_TYPES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

_EVENT_ADAPTER = TypeAdapter(Event)


def parse_event(data: str | bytes) -> Event:
    """Parse one event from its JSON text."""
    return _EVENT_ADAPTER.validate_json(data)
