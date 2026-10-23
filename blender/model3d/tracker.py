"""Follows one engine job by polling GET /v1/jobs/{id} (Blender's Python has no WebSocket client)."""

from dataclasses import dataclass

from .engine_client import EngineClient, EngineError, EngineUnreachable

ENGINE_DOWN_MESSAGE = "Open the Model3D desktop app"
FINAL_STATES = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class TrackerStatus:
    state: str
    stage: str | None
    progress: float
    error: dict | None
    lost: bool  # the engine stopped answering: the desktop app was closed

    @property
    def done(self) -> bool:
        return self.lost or self.state in FINAL_STATES


class JobTracker:
    def __init__(self, client: EngineClient, job_id: str, poll_s: float = 1.0, lost_after: int = 3):
        self.client = client
        self.job_id = job_id
        self.poll_s = poll_s
        self.lost_after = lost_after
        self._misses = 0
        self._last = TrackerStatus("queued", None, 0.0, None, False)

    def poll(self) -> TrackerStatus:
        """One poll; a few missed answers in a row mean the engine is gone."""
        try:
            job = self.client.job(self.job_id)
        except EngineUnreachable:
            self._misses += 1
            if self._misses >= self.lost_after:
                self._last = TrackerStatus(self._last.state, self._last.stage, self._last.progress, None, True)
            return self._last
        except EngineError as error:
            if error.code != "job_not_found":
                raise
            # The engine was restarted (the desktop app reopened): the job is gone with it.
            self._last = TrackerStatus(self._last.state, self._last.stage, self._last.progress, None, True)
            return self._last
        self._misses = 0
        self._last = TrackerStatus(job["state"], job.get("stage"), job.get("progress", 0.0), job.get("error"), False)
        return self._last
