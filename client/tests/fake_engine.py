"""A scriptable stand-in for the engine's HTTP and WebSocket API, served by uvicorn in a thread."""

import asyncio
import io
import json
import queue
import socket
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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


def make_health(ready: bool = True, texture: bool = True, error: dict | None = None, rig: bool = True,
                rig_reason: str | None = None, detailed_face: bool = False,
                detailed_face_reason: str | None = "Detailed face needs the TRELLIS.2 generator") -> dict:
    return {
        "ready": ready,
        "mode": {"name": "dual_gpu" if texture else "shape_only", "texture": texture, "reason": "test"},
        "gpus": [
            {"bus_id": "00000000:25:00.0", "stage": "shape", "ready": ready, "memory_used_mib": 100,
             "memory_total_mib": 12227, "memory_free_mib": 11000},
        ],
        "error": error,
        "rig": rig,
        "rig_reason": rig_reason,
        "detailed_face": detailed_face,
        "detailed_face_reason": None if detailed_face else detailed_face_reason,
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
    rig_submissions: list[dict] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    origins: list[str | None] = field(default_factory=list)
    original: bytes | None = b"glTF original model"
    result_requests: list = field(default_factory=list)
    library_dir: Path | None = None
    export_posts: list[tuple[str, str]] = field(default_factory=list)
    export_start: tuple[int, dict] | None = None
    export_answers: list[tuple[int, dict]] = field(default_factory=list)
    export_file: bytes = b"exported data"
    export_zip: dict[str, bytes] | None = None
    export_gets: list[tuple[str, str]] = field(default_factory=list)
    body_answer: tuple[int, dict] = (200, {"body": "biped", "legs": [], "arms": [], "up": 1})
    walk_posts: list[tuple[str, str]] = field(default_factory=list)
    walk: Callable[[str, str], tuple[int, dict]] | None = None
    voice_answers: dict[str, tuple[int, object]] = field(default_factory=dict)  # "METHOD /path" -> (status, body)
    voice_files: dict[str, bytes] = field(default_factory=dict)  # "GET /path" -> bytes answered with 200
    voice_requests: list[dict] = field(default_factory=list)

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

    @app.post("/v1/rig-jobs")
    async def submit_rig(request: Request):
        form = await request.form()
        model = form.get("model")
        c.rig_submissions.append({
            "filename": getattr(model, "filename", None),
            "content_type": getattr(model, "content_type", None),
            "model": await model.read() if model is not None else None,
        })
        status, body = c.submit
        return JSONResponse(status_code=status, content=body)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in c.jobs:
            return JSONResponse(status_code=404, content=error_body("job_not_found"))
        return c.jobs[job_id]

    @app.get("/v1/jobs/{job_id}/result")
    def result(job_id: str, variant: str | None = None):
        c.result_requests.append(variant)
        if variant == "original":
            if c.original is None:
                return JSONResponse(status_code=404, content=error_body("result_not_found", "no original model"))
            return Response(content=c.original, media_type="model/gltf-binary")
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

    @app.get("/v1/library/{entry_id}")
    def library_entry(entry_id: str):
        if c.library_dir is None or not (c.library_dir / entry_id / "entry.json").is_file():
            return JSONResponse(status_code=404, content=error_body("entry_not_found"))
        entry_path = c.library_dir / entry_id / "entry.json"
        data = json.loads(entry_path.read_text())
        entry_folder = c.library_dir / entry_id
        files = sorted(p.name for p in entry_folder.iterdir() if p.is_file() and p.name != "entry.json")
        data["files"] = files
        return data

    @app.post("/v1/library/{entry_id}/exports")
    async def start_export(entry_id: str, request: Request):
        body = await request.json()
        fmt = body["format"]
        c.export_posts.append((entry_id, fmt))
        if c.export_start is not None:
            status, content = c.export_start
            return JSONResponse(status_code=status, content=content)
        return JSONResponse(status_code=202, content={"format": fmt, "state": "running"})

    @app.get("/v1/library/{entry_id}/exports/{fmt}")
    def get_export(entry_id: str, fmt: str):
        c.export_gets.append((entry_id, fmt))
        if c.export_answers:
            status, content = c.export_answers.pop(0)
            return JSONResponse(status_code=status, content=content)
        if c.export_zip is not None:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for name, data in c.export_zip.items():
                    zf.writestr(name, data)
            return Response(content=buf.getvalue(), media_type="application/zip")
        return Response(content=c.export_file, media_type="application/octet-stream")

    @app.get("/v1/library/{entry_id}/body")
    def get_body(entry_id: str):
        status, content = c.body_answer
        return JSONResponse(status_code=status, content=content)

    @app.post("/v1/library/{entry_id}/walk")
    async def walk(entry_id: str, request: Request):
        body = await request.json()
        c.walk_posts.append((entry_id, body["body"]))
        if c.walk is None:
            return JSONResponse(status_code=409, content=error_body("no_skeleton"))
        status, content = c.walk(entry_id, body["body"])
        return JSONResponse(status_code=status, content=content)

    async def record(request: Request) -> dict:
        """Log a voice or character request: its method and path, plus a JSON body or the multipart fields."""
        entry: dict = {"method": request.method, "path": request.url.path, "json": None, "fields": {}, "files": {}}
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/"):
            for name, value in (await request.form()).items():
                if hasattr(value, "filename"):
                    entry["files"][name] = {"filename": value.filename, "content_type": value.content_type,
                                            "data": await value.read()}
                else:
                    entry["fields"][name] = value
        elif content_type.startswith("application/json"):
            entry["json"] = await request.json()
        c.voice_requests.append(entry)
        return entry

    @app.api_route(
        "/v1/{area:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def scripted(area: str, request: Request):
        """Answers the voice, speech and character routes from `voice_answers` / `voice_files`."""
        await record(request)
        key = f"{request.method} {request.url.path}"
        if key in c.voice_answers:
            status, body = c.voice_answers[key]
            if status == 204:
                return Response(status_code=204)
            return JSONResponse(status_code=status, content=body)
        if key in c.voice_files:
            return Response(content=c.voice_files[key], media_type="application/octet-stream")
        return JSONResponse(status_code=404, content=error_body("not_scripted", key))

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
