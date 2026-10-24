"""Job records, their state machine, and their folders on disk."""

import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from model3d_contracts.jobs import FINAL_STATES, Job, JobError, JobLinks, JobOptions, JobState

from model3d_engine.stages.protocol import RIG_INPUT

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_job_id(now_ms: int | None = None, rand: bytes | None = None) -> str:
    """Return a ULID: 26 Crockford base32 characters, sortable by creation time."""
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    if rand is None:
        rand = os.urandom(10)
    if len(rand) != 10:
        raise ValueError("rand must be exactly 10 bytes")
    value = (now_ms << 80) | int.from_bytes(rand, "big")
    chars = []
    for _ in range(26):
        value, remainder = divmod(value, 32)
        chars.append(_CROCKFORD[remainder])
    return "".join(reversed(chars))


class InvalidTransition(Exception):
    """A job was asked to move to a state its current state does not allow."""


_ALLOWED: dict[JobState, frozenset[JobState]] = {
    # queued -> rigging is a rig-only job: an existing model that only gets a skeleton.
    JobState.queued: frozenset({JobState.shaping, JobState.rigging, JobState.failed, JobState.cancelled}),
    JobState.shaping: frozenset(
        {JobState.painting, JobState.rigging, JobState.succeeded, JobState.failed, JobState.cancelled}
    ),
    JobState.painting: frozenset({JobState.rigging, JobState.succeeded, JobState.failed, JobState.cancelled}),
    JobState.rigging: frozenset({JobState.succeeded, JobState.failed, JobState.cancelled}),
    JobState.succeeded: frozenset(),
    JobState.failed: frozenset(),
    JobState.cancelled: frozenset(),
}


@dataclass
class JobRecord:
    """A job's mutable state while the engine runs."""

    job_id: str
    options: JobOptions
    seed: int
    dir: Path
    created_at: datetime
    state: JobState = JobState.queued
    stage: str | None = None
    progress: float = 0.0
    finished_at: datetime | None = None
    error: JobError | None = None
    cancel_requested: bool = False

    def transition(self, new: JobState, *, error: JobError | None = None) -> None:
        """Move to a new state, or raise InvalidTransition."""
        if new not in _ALLOWED[self.state]:
            raise InvalidTransition(f"{self.state.value} -> {new.value}")
        self.state = new
        if new == JobState.shaping:
            self.stage, self.progress = "shape", 0.0
        elif new == JobState.painting:
            self.stage, self.progress = "paint", 0.0
        elif new == JobState.rigging:
            self.stage, self.progress = "rig", 0.0
        elif new in FINAL_STATES:
            self.stage = None
            self.finished_at = datetime.now(UTC)
            self.error = error

    @property
    def is_final(self) -> bool:
        """Whether the job reached succeeded, failed, or cancelled."""
        return self.state in FINAL_STATES

    def to_job(self, queue_position: int | None) -> Job:
        """The API representation of this job."""
        base = f"/v1/jobs/{self.job_id}"
        return Job(
            job_id=self.job_id,
            state=self.state,
            stage=self.stage,
            progress=self.progress,
            queue_position=queue_position,
            seed=self.seed,
            created_at=self.created_at,
            finished_at=self.finished_at,
            error=self.error,
            links=JobLinks(self=base, events=f"{base}/events", result=f"{base}/result"),
        )


class JobStore:
    """Job records in memory, each with a folder under jobs_dir."""

    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self._records: dict[str, JobRecord] = {}

    def create(self, options: JobOptions, image_bytes: bytes, image_suffix: str) -> JobRecord:
        """Register a new queued job and write its input image."""
        return self._new(options, f"input{image_suffix}", image_bytes)

    def create_rig(self, model_bytes: bytes, textured: bool) -> JobRecord:
        """Register a rig-only job for an existing model (GLB bytes)."""
        return self._new(JobOptions(rig=True, texture=textured), RIG_INPUT, model_bytes)

    def _new(self, options: JobOptions, input_name: str, data: bytes) -> JobRecord:
        job_id = new_job_id()
        folder = self.jobs_dir / job_id
        folder.mkdir(parents=True)
        (folder / input_name).write_bytes(data)
        seed = options.seed if options.seed is not None else secrets.randbelow(2**32)
        record = JobRecord(job_id=job_id, options=options, seed=seed, dir=folder, created_at=datetime.now(UTC))
        self._records[job_id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        """The record for job_id, or None."""
        return self._records.get(job_id)

    def persist(self, record: JobRecord) -> None:
        """Write the job's current state to job.json in its folder."""
        (record.dir / "job.json").write_text(record.to_job(None).model_dump_json(indent=2))
