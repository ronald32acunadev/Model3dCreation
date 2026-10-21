"""REST and WebSocket API of the engine."""

import io
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import ValidationError

from model3d_contracts.jobs import ErrorBody, Job, JobError, JobOptions, JobState

from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobRecord, JobStore
from model3d_engine.stages.protocol import MODEL_OUTPUT

_SUFFIXES = {"PNG": ".png", "JPEG": ".jpg"}


class ApiError(Exception):
    """An error answered with an ErrorBody and an HTTP status."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _check_image(data: bytes) -> str:
    """Return the file suffix for a PNG or JPEG upload, or raise invalid_image."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            image.verify()
    except Exception as error:
        raise ApiError(400, "invalid_image", "Upload a PNG or JPEG image") from error
    if fmt not in _SUFFIXES:
        raise ApiError(400, "invalid_image", "Upload a PNG or JPEG image")
    return _SUFFIXES[fmt]


def _parse_options(text: str) -> JobOptions:
    """Parse the options form field, or raise invalid_options with the first error."""
    try:
        return JobOptions.model_validate_json(text)
    except ValidationError as error:
        message = error.errors()[0]["msg"].removeprefix("Value error, ")
        raise ApiError(400, "invalid_options", message) from error


def create_app(
    settings: Settings, scheduler: Scheduler, store: JobStore, bus: EventBus, health: Callable[[], dict]
) -> FastAPI:
    """Build the API around an existing scheduler, store, and event bus."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await scheduler.start()
        yield
        await scheduler.stop()

    app = FastAPI(title="model3d engine", lifespan=lifespan)
    app.state.bus = bus

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        body = ErrorBody(error=JobError(code=error.code, message=error.message))
        return JSONResponse(status_code=error.status, content=body.model_dump())

    def _record(job_id: str) -> JobRecord:
        record = store.get(job_id)
        if record is None:
            raise ApiError(404, "job_not_found", f"No job {job_id}")
        return record

    @app.post("/v1/jobs", status_code=202, response_model=Job)
    async def submit(image: UploadFile | None = File(None), options: str = Form("{}")) -> Job:
        if image is None:
            raise ApiError(400, "invalid_image", "Missing image file")
        data = await image.read(settings.max_image_bytes + 1)
        if len(data) > settings.max_image_bytes:
            raise ApiError(413, "image_too_large", f"Images are limited to {settings.max_image_bytes} bytes")
        suffix = _check_image(data)
        job_options = _parse_options(options or "{}")
        if not scheduler.ready:
            raise ApiError(503, "engine_not_ready", "The engine is still loading its models")
        record = store.create(job_options, data, suffix)
        scheduler.submit(record)
        return record.to_job(scheduler.queue_position(record.job_id))

    @app.get("/v1/jobs/{job_id}", response_model=Job)
    async def get_job(job_id: str) -> Job:
        return _record(job_id).to_job(scheduler.queue_position(job_id))

    @app.get("/v1/jobs/{job_id}/result")
    async def get_result(job_id: str) -> FileResponse:
        record = _record(job_id)
        if record.state != JobState.succeeded:
            raise ApiError(409, "job_not_finished", f"Job {job_id} is {record.state.value}")
        return FileResponse(record.dir / MODEL_OUTPUT, media_type="model/gltf-binary", filename="model.glb")

    @app.delete("/v1/jobs/{job_id}", status_code=202, response_model=Job)
    async def cancel_job(job_id: str) -> Job:
        record = _record(job_id)
        if not scheduler.cancel(job_id):
            raise ApiError(409, "job_finished", f"Job {job_id} already {record.state.value}")
        return record.to_job(scheduler.queue_position(job_id))

    @app.websocket("/v1/jobs/{job_id}/events")
    async def job_events(websocket: WebSocket, job_id: str) -> None:
        record = store.get(job_id)
        if record is None:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        events = bus.subscribe(record)
        try:
            async for event in events:
                await websocket.send_text(event.model_dump_json())
            await websocket.close(code=1000)
        except (WebSocketDisconnect, RuntimeError):
            return  # the client left
        finally:
            await events.aclose()

    @app.get("/v1/health")
    async def get_health() -> dict:
        return health()

    return app
