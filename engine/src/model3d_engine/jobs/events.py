"""Per-job event fan-out with a snapshot for late subscribers."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from model3d_contracts.events import FINAL_EVENT_TYPES, Event, SnapshotEvent

from model3d_engine.jobs.store import JobRecord


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


class EventBus:
    """Delivers each job's events to every subscriber of that job."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._final: dict[str, Event] = {}

    def publish(self, event: Event) -> None:
        """Queue an event for the job's subscribers; keep final events for late ones."""
        if event.type in FINAL_EVENT_TYPES:
            self._final[event.job_id] = event
        for queue in self._subscribers.get(event.job_id, []):
            queue.put_nowait(event)

    async def subscribe(self, record: JobRecord) -> AsyncIterator[Event]:
        """Yield a snapshot, then the job's events, ending after a final event."""
        queue: asyncio.Queue = asyncio.Queue()
        # Decide at subscription time: a job that already ended replays its final event;
        # otherwise register before the snapshot so nothing published after it is lost.
        final = self._final.get(record.job_id)
        self._subscribers.setdefault(record.job_id, []).append(queue)
        try:
            yield SnapshotEvent(
                job_id=record.job_id, ts=utcnow(), state=record.state, stage=record.stage, progress=record.progress
            )
            if final is not None:
                yield final
                return
            while True:
                event = await queue.get()
                yield event
                if event.type in FINAL_EVENT_TYPES:
                    return
        finally:
            queues = self._subscribers.get(record.job_id, [])
            if queue in queues:
                queues.remove(queue)
            if not queues:
                self._subscribers.pop(record.job_id, None)
