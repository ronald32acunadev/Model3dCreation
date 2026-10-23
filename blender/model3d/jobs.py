"""What the add-on's worker thread does: check the engine, write the library entry, submit,
follow the job, and download the result. No bpy here, so it runs in plain Python tests.

The UI's timer reads JobStatus.snapshot() and imports `pending_import` on Blender's main thread.
"""

import json
import threading
import time
from pathlib import Path

from . import library
from .engine_client import EngineClient, EngineError, EngineUnreachable
from .tracker import ENGINE_DOWN_MESSAGE, JobTracker


class JobStatus:
    """State shared between the worker thread and the UI; every access holds the lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self.busy = False
        self.stage: str | None = None
        self.progress = 0.0
        self.message = ""
        self.error: tuple[str, str] | None = None
        self.job_id: str | None = None
        self.cancel_requested = False
        self.pending_import: Path | None = None

    def update(self, **fields) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self, name, value)

    def snapshot(self) -> dict:
        with self._lock:
            return {name: value for name, value in vars(self).items() if not name.startswith("_")}

    def take_import(self) -> Path | None:
        """The finished model to import, once."""
        with self._lock:
            path, self.pending_import = self.pending_import, None
            return path

    def fail(self, code: str, message: str) -> None:
        self.update(error=(code, message), message=message, stage=None)


def _check_engine(client: EngineClient, status: JobStatus, rig: bool) -> bool:
    try:
        health = client.health()
    except EngineUnreachable:
        status.fail("engine_unreachable", ENGINE_DOWN_MESSAGE)
        return False
    except EngineError as error:
        status.fail(error.code, error.message)
        return False
    if not health.get("ready"):
        status.fail("engine_not_ready", "The engine is still loading")
        return False
    if rig and not health.get("rig"):
        status.fail("rig_unavailable", health.get("rig_reason") or "This engine cannot add skeletons")
        return False
    return True


def _finish(root: Path, entry: dict, status: JobStatus, outcome: str, error: dict | None = None) -> None:
    library.update_entry(root, {**entry, "outcome": outcome, "error": error})
    if error is not None:
        status.fail(error["code"], ENGINE_DOWN_MESSAGE if error["code"] == "engine_lost" else error["message"])


def _follow(client: EngineClient, root: Path, entry: dict, submit, status: JobStatus, poll_s: float) -> None:
    """Submit, poll until the job ends, and record the outcome in the entry."""
    try:
        job = submit()
    except EngineUnreachable:
        _finish(root, entry, status, "failed", {"code": "engine_lost", "message": "The engine stopped answering"})
        return
    except EngineError as error:
        _finish(root, entry, status, "failed", {"code": error.code, "message": error.message})
        return
    entry = {**entry, "job_id": job["job_id"], "seed": job.get("seed")}
    library.update_entry(root, entry)
    status.update(job_id=job["job_id"], message="Waiting in the queue…")
    tracker = JobTracker(client, job["job_id"])
    cancel_sent = False
    while True:
        if status.snapshot()["cancel_requested"] and not cancel_sent:
            cancel_sent = True
            try:
                client.cancel(job["job_id"])
            except EngineError:
                pass  # the job already ended, or the engine is gone; the next poll tells
        current = tracker.poll()
        status.update(stage=current.stage, progress=current.progress)
        if current.done:
            break
        time.sleep(poll_s)
    if current.lost:
        _finish(root, entry, status, "failed", {"code": "engine_lost", "message": "The engine stopped answering"})
    elif current.state == "succeeded":
        target = library.model_path(root, entry["entry_id"])
        try:
            client.download(job["job_id"], target)
        except EngineError as error:
            _finish(root, entry, status, "failed", {"code": error.code, "message": error.message})
            return
        _finish(root, entry, status, "succeeded")
        status.update(pending_import=target, message="Done", stage=None)
    elif current.state == "cancelled":
        _finish(root, entry, status, "cancelled")
        status.update(message="Cancelled", stage=None)
    else:
        _finish(root, entry, status, "failed", current.error or {"code": "internal_error", "message": "The job failed"})


def _fail_unexpected(root: Path, entry_id: str | None, status: JobStatus, error: Exception) -> None:
    """An error nothing above expected: fail the entry if it is still running, and say why."""
    message = f"{type(error).__name__}: {error}"
    if entry_id is not None:
        try:
            entry = json.loads((root / entry_id / library.ENTRY_FILE).read_text())
            if entry.get("outcome") == "running":
                library.update_entry(root, {**entry, "outcome": "failed",
                                            "error": {"code": "internal_error", "message": message}})
        except (OSError, ValueError):
            pass
    status.fail("internal_error", message)


def run_generation(client: EngineClient, root: Path, image: Path, options: dict, status: JobStatus,
                   poll_s: float = 1.0) -> None:
    """Generate a model from an image into a new library entry (worker thread)."""
    status.update(busy=True, error=None, stage=None, progress=0.0, job_id=None, message="Checking the engine…")
    entry = None
    try:
        if not _check_engine(client, status, bool(options.get("rig"))):
            return
        entry = library.create_entry(root, image, options)
        status.update(message="Sending the image…")
        _follow(client, root, entry, lambda: client.submit_image(image, options), status, poll_s)
    except Exception as error:
        _fail_unexpected(root, entry["entry_id"] if entry else None, status, error)
    finally:
        status.update(busy=False, cancel_requested=False)


def run_add_skeleton(client: EngineClient, root: Path, source: dict, status: JobStatus,
                     poll_s: float = 1.0) -> None:
    """Rig an existing entry's model into a new entry; the source entry is never written (worker thread)."""
    status.update(busy=True, error=None, stage=None, progress=0.0, job_id=None, message="Checking the engine…")
    entry = None
    try:
        if not _check_engine(client, status, True):
            return
        image = root / source["entry_id"] / source["input_file"]
        options = {**source.get("options", {}), "rig": True}
        entry = library.create_entry(root, image, options, name=f"{source['name']} (rigged)",
                                     source_entry_id=source["entry_id"])
        model = library.model_path(root, source["entry_id"])
        status.update(message="Sending the model…")
        _follow(client, root, entry, lambda: client.submit_rig(model), status, poll_s)
    except Exception as error:
        _fail_unexpected(root, entry["entry_id"] if entry else None, status, error)
    finally:
        status.update(busy=False, cancel_requested=False)
