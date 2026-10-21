"""A scriptable stand-in for the engine's HTTP and WebSocket API, served by uvicorn in a thread."""

import asyncio
import queue
import socket
import threading
import time
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from model3d_contracts.jobs import Job, JobLinks, JobState

CLOSE = object()  # put on the event queue to close the socket


def make_job(job_id: str = "J1", state: JobState = JobState.queued, seed: int = 42) -> dict:
    base = f"/v1/jobs/{job_id}"
    job = Job(
        job_id=job_id,
        state=state,
        seed=seed,
        queue_position=0 if state == JobState.queued else None,
        created_at="2026-10-21T10:00:00Z",
        links=JobLinks(self=base, events=f"{base}/events", result=f"{base}/result"),
    )
    return job.model_dump(mode="json")


def make_health(ready: bool = True, texture: bool = True, error: dict | None = None) -> dict:
    return {
        "ready": ready,
        "mode": {"name": "dual_gpu" if texture else "shape_only", "texture": texture, "reason": "test"},
        "gpus": [
            {"bus_id": "00000000:25:00.0", "stage": "shape", "ready": ready, "memory_used_mib": 100,
             "memory_total_mib": 12227, "memory_free_mib": 11000},
        ],
        "error": error,
    }


def error_body(code: str, message: str = "") -> dict:
    return {"error": {"code": code, "message": message or code}}


@dataclass
class Controller:
    """What the fake engine answers, and what it received."""

    health: dict = field(default_factory=make_health)
    submit: tuple[int, dict] = field(default_factory=lambda: (202, make_job()))
    cancel: tuple[int, dict] | None = None  # default: 202 with the job as cancelling requested
    jobs: dict[str, dict] = field(default_factory=dict)
    result: bytes = b"glTF fake model"
    events: "queue.Queue" = field(default_factory=queue.Queue)
    submissions: list[dict] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    origins: list[str | None] = field(default_factory=list)

    def push(self, *events) -> None:
        """Queue events (dicts) or CLOSE for the job's socket."""
        for event in events:
            self.events.put(event)


def create_app(c: Controller) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/health")
    def health():
        return c.health

    @app.post("/v1/jobs")
    async def submit(request: Request):
        form = await request.form()
        image = form.get("image")
        c.origins.append(request.headers.get("origin"))
        c.submissions.append({
            "options": form.get("options"),
            "filename": getattr(image, "filename", None),
            "image": await image.read() if image is not None else None,
        })
        status, body = c.submit
        return JSONResponse(status_code=status, content=body)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in c.jobs:
            return JSONResponse(status_code=404, content=error_body("job_not_found"))
        return c.jobs[job_id]

    @app.get("/v1/jobs/{job_id}/result")
    def result(job_id: str):
        return Response(content=c.result, media_type="model/gltf-binary")

    @app.delete("/v1/jobs/{job_id}")
    def cancel(job_id: str, request: Request):
        c.origins.append(request.headers.get("origin"))
        c.cancels.append(job_id)
        if c.cancel is not None:
            return JSONResponse(status_code=c.cancel[0], content=c.cancel[1])
        return JSONResponse(status_code=202, content=make_job(job_id, JobState.shaping))

    @app.websocket("/v1/jobs/{job_id}/events")
    async def events(websocket: WebSocket, job_id: str):
        c.origins.append(websocket.headers.get("origin"))
        await websocket.accept()
        while True:
            try:
                item = c.events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if item is CLOSE:
                await websocket.close(code=1000)
                return
            await websocket.send_json(item)

    return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeEngine:
    def __init__(self, port: int | None = None):
        self.controller = Controller()
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(create_app(self.controller), host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "FakeEngine":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("fake engine did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self.controller.push(CLOSE)
        self.server.should_exit = True
        self.thread.join(timeout=10)
