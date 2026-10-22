"""Job resources exchanged over the engine's REST API."""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator


class JobState(str, Enum):
    """Job execution state."""

    queued = "queued"
    shaping = "shaping"
    painting = "painting"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


FINAL_STATES: frozenset[JobState] = frozenset({JobState.succeeded, JobState.failed, JobState.cancelled})


class Quality(str, Enum):
    """Quality preset for generation."""

    draft = "draft"
    final = "final"
    high = "high"


def _serialize_utc(value: datetime) -> str:
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


# Datetimes serialize as ISO 8601 UTC with a Z suffix, e.g. 2026-10-20T19:05:00Z.
UtcDatetime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str, when_used="json")]


class JobOptions(BaseModel):
    """Options a client sends with a new job."""

    model_config = ConfigDict(extra="forbid")

    texture: bool = True
    quality: Quality = Quality.final
    seed: int | None = Field(default=None, ge=0, lt=2**32)
    remove_background: bool = True
    target_faces: int | None = None

    @field_validator("target_faces")
    @classmethod
    def _check_target_faces(cls, value: int | None) -> int | None:
        if value is not None and value < 1000:
            raise ValueError("target_faces must be at least 1000")
        return value


class JobLinks(BaseModel):
    """URLs of a job's related resources."""

    self: str
    events: str
    result: str


class JobError(BaseModel):
    """Stable error code with a human-readable message."""

    code: str
    message: str


class ErrorBody(BaseModel):
    """Body of every error response."""

    error: JobError


Stage = Literal["shape", "paint"]


class Job(BaseModel):
    """A generation job as returned by the API."""

    job_id: str
    state: JobState
    stage: Stage | None = None
    progress: float = 0.0
    queue_position: int | None = None
    seed: int
    created_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    error: JobError | None = None
    links: JobLinks
