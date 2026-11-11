"""REST and WebSocket API of the engine."""

import asyncio
import hmac
import io
import json
import logging
import struct
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from model3d_contracts.health import Health
from model3d_contracts.jobs import ErrorBody, Job, JobError, JobOptions, JobState

from model3d_contracts.characters import Characters, Phrase
from model3d_contracts.library import Library

from model3d_engine.api.characters import characters_router
from model3d_engine.api.errors import ApiError
from model3d_engine.api.library import library_router
from model3d_engine.api.speech import SPEECH_OUTPUT, speech_result, speech_router
from model3d_engine.api.tasks import ToolTasks
from model3d_engine.api.voices import voices_router
from model3d_engine.config import Settings, is_loopback_host, normalize_origin
from model3d_engine.jobs.events import EventBus, utcnow
from model3d_engine.jobs.scheduler import ORIGINAL_OUTPUT, Scheduler
from model3d_engine.jobs.store import REQUEST_FILE, JobRecord, JobStore
from model3d_engine.stages.protocol import MODEL_OUTPUT
from model3d_engine.voices import VoiceStore

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
_SUFFIXES = {"PNG": ".png", "JPEG": ".jpg"}
_STATE_CHANGING = {"POST", "DELETE"}
logger = logging.getLogger(__name__)


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


def _glb_json(data: bytes) -> dict:
    """The JSON chunk of a complete GLB 2.0 file, or raise invalid_model."""
    invalid = ApiError(400, "invalid_model", "Upload a binary glTF 2.0 (.glb) model")
    if len(data) < 20 or data[:4] != b"glTF":
        raise invalid
    version, length = struct.unpack_from("<II", data, 4)
    chunk_length, chunk_type = struct.unpack_from("<I4s", data, 12)
    if version != 2 or length != len(data) or chunk_type != b"JSON" or 20 + chunk_length > len(data):
        raise invalid
    try:
        document = json.loads(data[20 : 20 + chunk_length])
    except ValueError as error:
        raise invalid from error
    if not isinstance(document, dict):
        raise invalid
    return document


def glb_has_images(data: bytes) -> bool:
    """Whether a GLB carries textures (a non-empty images array)."""
    return bool(_glb_json(data).get("images"))


def _parse_options(text: str) -> JobOptions:
    """Parse the options form field, or raise invalid_options with the first error."""
    try:
        return JobOptions.model_validate_json(text)
    except ValidationError as error:
        message = error.errors()[0]["msg"].removeprefix("Value error, ")
        raise ApiError(400, "invalid_options", message) from error


def _host_name(host: str) -> str:
    """Host header without its port or IPv6 brackets, lowercased."""
    host = host.lower()
    if host.startswith("["):
        return host[1 : host.find("]")]
    return host.rsplit(":", 1)[0]


class LoopbackGuard:
    """Blocks browser pages from driving the engine: rejects a Host that is neither loopback nor
    the LAN address (DNS rebinding) and a POST or DELETE whose Origin is not allowed (CSRF).

    Clients that send no Origin header, like curl and the desktop app, are not affected.
    """

    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: tuple[str, ...],
        allowed_hosts: tuple[str, ...] = (),
        api_keys: tuple[str, ...] = (),
    ):
        self.app = app
        self.allowed_origins = {normalize_origin(origin) for origin in allowed_origins}
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.api_keys = tuple(key.encode() for key in api_keys)

    def _rejection(self, scope: Scope) -> tuple[int, str, str] | None:
        headers = Headers(scope=scope)
        host = _host_name(headers.get("host", ""))
        if not (is_loopback_host(host) or host in self.allowed_hosts):
            return 403, "forbidden_origin", "The Host header must name a loopback address or the LAN address"
        if not self._is_local(scope) and not self._has_valid_key(headers):
            return 401, "unauthorized", "Send Authorization: Bearer <key> to use the engine from the network"
        origin = headers.get("origin")
        if scope["type"] == "http" and scope["method"] in _STATE_CHANGING and origin is not None:
            if normalize_origin(origin) not in self.allowed_origins:
                return 403, "forbidden_origin", f"Origin {origin} may not change engine state"
        return None

    @staticmethod
    def _is_local(scope: Scope) -> bool:
        """Whether the connection arrived on a loopback address; unknown addresses count as remote."""
        server = scope.get("server")
        return server is not None and is_loopback_host(server[0])

    def _has_valid_key(self, headers: Headers) -> bool:
        authorization = headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return False
        presented = authorization[len("Bearer ") :].encode()
        matched = False
        for key in self.api_keys:
            matched |= hmac.compare_digest(presented, key)
        return matched

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or (rejection := self._rejection(scope)) is None:
            await self.app(scope, receive, send)
            return
        status, code, message = rejection
        if scope["type"] == "websocket":
            # Closed before accept: the server answers the handshake with 403.
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
        else:
            body = ErrorBody(error=JobError(code=code, message=message))
            await JSONResponse(status_code=status, content=body.model_dump())(scope, receive, send)


def _log_startup_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Engine failed to start: %s", task.exception())


def create_app(
    settings: Settings,
    scheduler: Scheduler,
    store: JobStore,
    bus: EventBus,
    health: Callable[[], dict],
    texture_available: bool = True,
    rig_reason: Callable[[], str | None] = lambda: None,
    detailed_face_reason: Callable[[], str | None] = lambda: "Detailed face is not set up",
    tool_tasks: ToolTasks | None = None,
    voice_reason: Callable[[], str | None] = lambda: "Voice generation is not set up",
) -> FastAPI:
    """Build the API around an existing scheduler, store, and event bus.

    `health` returns the body of GET /v1/health without its `error`, which comes from the
    scheduler; `texture_available` is false when the run mode has no paint stage; `rig_reason`
    explains why rigging is unavailable; `detailed_face_reason` explains why Detailed face is
    unavailable (None when it is available); `voice_reason` explains why speech jobs are unavailable;
    `tool_tasks` runs the export and walk scripts (the
    engine's own Python and `tools/` by default).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The scheduler loads no model at start, so starting only starts its loops. A start error
        # set before startup (no usable GPU) means the loops must not run.
        startup = None
        if scheduler.start_error is None:
            startup = asyncio.create_task(scheduler.start())
            startup.add_done_callback(_log_startup_failure)
        yield
        if startup is not None:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        await scheduler.stop()

    app = FastAPI(title="model3d engine", lifespan=lifespan)
    app.add_middleware(
        LoopbackGuard,
        allowed_origins=settings.allowed_origins,
        allowed_hosts=(settings.lan_host,) if settings.lan_host else (),
        api_keys=settings.api_keys,
    )
    app.state.bus = bus
    tasks = tool_tasks or ToolTasks(sys.executable, TOOLS_DIR)
    library = Library(settings.home / "library", recover=False)
    app.include_router(library_router(library, store, tasks))
    characters = Characters(library)
    voices = VoiceStore(settings.voices_dir)
    app.include_router(characters_router(characters, voices, store, scheduler, voice_reason))
    app.include_router(voices_router(voices, characters))

    def keep_phrase(record: JobRecord) -> None:
        request = json.loads((record.dir / REQUEST_FILE).read_text(encoding="utf-8"))
        character_id = request.get("character_id")
        if character_id is None:
            return
        phrase = Phrase(
            phrase_id=record.job_id, text=request["text"], language=request["language"], created_at=utcnow()
        )
        try:
            characters.add_phrase(character_id, phrase, record.dir / SPEECH_OUTPUT)
        except KeyError:
            logger.warning("Character %s no longer exists; phrase %s not kept", character_id, record.job_id)

    scheduler.on_speech_succeeded = keep_phrase
    app.include_router(speech_router(scheduler, store, voices, voice_reason))

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
        if job_options.texture and not texture_available:
            raise ApiError(400, "texture_unavailable", 'This engine runs without the paint stage: send "texture": false')
        if job_options.rig:
            _check_rig()
        if job_options.detailed_face and (reason := detailed_face_reason()) is not None:
            raise ApiError(400, "detailed_face_unavailable", reason)
        _check_ready()
        record = store.create(job_options, data, suffix)
        scheduler.submit(record)
        return record.to_job(scheduler.queue_position(record.job_id))

    @app.post("/v1/rig-jobs", status_code=202, response_model=Job)
    async def submit_rig(model: UploadFile | None = File(None)) -> Job:
        if model is None:
            raise ApiError(400, "invalid_model", "Missing model file")
        data = await model.read(settings.max_model_bytes + 1)
        if len(data) > settings.max_model_bytes:
            raise ApiError(413, "model_too_large", f"Models are limited to {settings.max_model_bytes} bytes")
        textured = glb_has_images(data)
        _check_rig()
        _check_ready()
        record = store.create_rig(data, textured)
        scheduler.submit_rig(record)
        return record.to_job(None)

    def _check_rig() -> None:
        if not scheduler.rig_available:
            raise ApiError(400, "rig_unavailable", rig_reason() or "This engine cannot add skeletons")

    def _check_ready() -> None:
        if scheduler.start_error is not None:
            raise ApiError(503, "engine_not_ready", f"The engine failed to start: {scheduler.start_error.message}")
        if not scheduler.ready:
            raise ApiError(503, "engine_not_ready", "The engine is still loading its models")

    @app.get("/v1/jobs/{job_id}", response_model=Job)
    async def get_job(job_id: str) -> Job:
        return _record(job_id).to_job(scheduler.queue_position(job_id))

    @app.get("/v1/jobs/{job_id}/result")
    async def get_result(job_id: str, variant: str | None = None) -> FileResponse:
        record = _record(job_id)
        if record.state != JobState.succeeded:
            raise ApiError(409, "job_not_finished", f"Job {job_id} is {record.state.value}")
        if record.kind == "speech":
            return speech_result(record)
        if variant == "original":
            original = record.dir / ORIGINAL_OUTPUT
            if not original.is_file():
                raise ApiError(404, "result_not_found", f"Job {job_id} has no original model")
            return FileResponse(original, media_type="model/gltf-binary", filename="model_original.glb")
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
    def get_health() -> dict:
        # A plain def runs in the threadpool, so a slow nvidia-smi never blocks the event loop.
        body = Health.model_validate({**health(), "error": scheduler.start_error})
        return body.model_dump(mode="json")

    return app
