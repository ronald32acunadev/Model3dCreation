"""Library API: list, save, download, export, and animate library models."""

import io
import re
import shutil
import zipfile

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict

from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobError, JobState
from model3d_contracts.library import ENTRY_FILE, Entry, Library, Outcome
from model3d_engine.api.errors import ApiError
from model3d_engine.api.tasks import ToolBusy, ToolResult, ToolTasks, failure_reason, parse_result
from model3d_engine.jobs.scheduler import STATS_FILE
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.protocol import MODEL_OUTPUT

ORIGINAL_MODEL = "model_original.glb"
_ENTRY_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]*")
EXPORT_FORMATS = ("fbx", "obj", "stl", "usdz")
BODIES = ("biped", "quadruped")


class EntryView(Entry):
    """A library entry with the names of its files."""
    files: list[str]


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    name: str | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: str


class ExportAccepted(BaseModel):
    format: str
    state: str


class WalkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str


def library_router(library: Library, store: JobStore, tasks: ToolTasks) -> APIRouter:
    router = APIRouter()

    def _find(entry_id: str) -> Entry:
        if _ENTRY_ID.fullmatch(entry_id) is None:
            raise ApiError(404, "entry_not_found", f"No library entry {entry_id}")
        for entry in library.entries():
            if entry.entry_id == entry_id:
                return entry
        raise ApiError(404, "entry_not_found", f"No library entry {entry_id}")

    def _files(entry_id: str) -> list[str]:
        folder = library.folder(entry_id)
        names = [p.name for p in folder.iterdir() if p.is_file() and p.name != ENTRY_FILE]
        return sorted(names)

    def _view(entry: Entry) -> EntryView:
        return EntryView(**entry.model_dump(), files=_files(entry.entry_id))

    def _ready(entry_id: str) -> Entry:
        entry = _find(entry_id)
        if entry.outcome != Outcome.succeeded or not library.model_path(entry_id).is_file():
            raise ApiError(409, "model_not_ready", f"Library entry {entry_id} has no finished model")
        return entry

    @router.get("/v1/library", response_model=list[EntryView])
    async def list_library() -> list[EntryView]:
        return [_view(entry) for entry in library.entries()]

    @router.get("/v1/library/{entry_id}", response_model=EntryView)
    async def get_entry(entry_id: str) -> EntryView:
        return _view(_find(entry_id))

    @router.get("/v1/library/{entry_id}/files/{name}")
    async def download_file(entry_id: str, name: str) -> FileResponse:
        _find(entry_id)
        if name not in _files(entry_id):
            raise ApiError(404, "file_not_found", f"No file {name} in library entry {entry_id}")
        return FileResponse(library.folder(entry_id) / name)

    @router.post("/v1/library", status_code=201, response_model=EntryView)
    async def save_entry(body: SaveRequest) -> EntryView:
        record = store.get(body.job_id)
        if record is None:
            raise ApiError(404, "job_not_found", f"No job {body.job_id}")
        if record.state != JobState.succeeded or not (record.dir / MODEL_OUTPUT).is_file():
            raise ApiError(409, "job_not_finished", "Only finished jobs can be saved to the library")
        image = next(record.dir.glob("input.*"), None)
        if image is None:
            raise ApiError(409, "job_has_no_image", "Only jobs made from an image can be saved to the library")
        name = body.name.strip() if body.name and body.name.strip() else record.job_id
        entry = library.create(image, record.options, name=name)
        try:
            shutil.copyfile(record.dir / MODEL_OUTPUT, library.model_path(entry.entry_id))
            original = record.dir / ORIGINAL_MODEL
            if original.is_file():
                shutil.copyfile(original, library.folder(entry.entry_id) / ORIGINAL_MODEL)
        except OSError:
            shutil.rmtree(library.folder(entry.entry_id), ignore_errors=True)
            raise
        stats_path = record.dir / STATS_FILE
        stats = ResultStats.model_validate_json(stats_path.read_bytes()) if stats_path.is_file() else None
        entry = entry.model_copy(update={"outcome": Outcome.succeeded, "seed": record.seed, "job_id": record.job_id, "stats": stats})
        library.update(entry)
        return _view(entry)

    @router.post("/v1/library/{entry_id}/exports", status_code=202, response_model=ExportAccepted)
    async def start_export(entry_id: str, body: ExportRequest) -> ExportAccepted:
        _find(entry_id)
        fmt = body.format
        if fmt not in EXPORT_FORMATS:
            raise ApiError(422, "invalid_format", f"Unknown format {fmt}; use one of {', '.join(EXPORT_FORMATS)}")
        _ready(entry_id)
        if tasks.is_busy(entry_id):
            raise ApiError(409, "entry_busy", "A tool is already running for this entry")
        folder = library.folder(entry_id) / "exports" / fmt
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        target = folder / f"model.{fmt}"

        def finish(result: ToolResult) -> str | None:
            if result.code == 0 and target.is_file() and target.stat().st_size > 0:
                return None
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            return failure_reason(result.stderr, result.code, f"Conversion failed with exit code {result.code}")

        try:
            tasks.start(entry_id, f"export:{fmt}", "convert_model.py", [str(library.model_path(entry_id)), str(target)], finish)
        except ToolBusy:
            raise ApiError(409, "entry_busy", "A tool is already running for this entry")
        return ExportAccepted(format=fmt, state="running")

    @router.get("/v1/library/{entry_id}/exports/{fmt}")
    async def get_export(entry_id: str, fmt: str):
        _find(entry_id)
        if fmt not in EXPORT_FORMATS:
            raise ApiError(422, "invalid_format", f"Unknown format {fmt}; use one of {', '.join(EXPORT_FORMATS)}")
        run = tasks.status(entry_id, f"export:{fmt}")
        folder = library.folder(entry_id) / "exports" / fmt
        files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []
        if (run is None and files) or (run is not None and run.state == "succeeded" and files):
            if len(files) == 1:
                return FileResponse(files[0], filename=files[0].name)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in files:
                    zf.write(path, path.relative_to(folder).as_posix())
            return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{entry_id}-{fmt}.zip"'})
        if run is not None and run.state == "running":
            raise ApiError(409, "export_not_ready", f"Export {fmt} is running")
        if run is not None and run.state == "failed":
            raise ApiError(409, "export_not_ready", f"Export {fmt} failed: {run.error}")
        raise ApiError(409, "export_not_ready", f"Export {fmt} was not started")

    @router.get("/v1/library/{entry_id}/body")
    async def detect_body(entry_id: str) -> dict:
        entry = _find(entry_id)
        if not Library.has_skeleton(entry):
            raise ApiError(409, "no_skeleton", f"Library entry {entry_id} has no skeleton")
        _ready(entry_id)
        try:
            result = await tasks.run(entry_id, "animate_rig.py", ["detect", str(library.model_path(entry_id))])
        except ToolBusy:
            raise ApiError(409, "entry_busy", "A tool is already running for this entry")
        body = parse_result(result.stdout)
        if result.code != 0 or body is None:
            raise ApiError(500, "detect_failed", failure_reason(result.stderr, result.code, "The script gave no result" if result.code == 0 else f"Animation failed with exit code {result.code}"))
        return body

    @router.post("/v1/library/{entry_id}/walk", status_code=202, response_model=EntryView)
    async def start_walk(entry_id: str, body: WalkRequest) -> EntryView:
        source = _find(entry_id)
        body_type = body.body
        if body_type not in BODIES:
            raise ApiError(422, "invalid_body", f"Unknown body type {body_type}; use biped or quadruped")
        if not Library.has_skeleton(source):
            raise ApiError(409, "no_skeleton", f"Library entry {entry_id} has no skeleton")
        _ready(entry_id)
        if tasks.is_busy(entry_id):
            raise ApiError(409, "entry_busy", "A tool is already running for this entry")
        derived = library.create_derived(entry_id, suffix=" (walk)")
        target = library.model_path(derived.entry_id)
        view = _view(derived)

        def finish(result: ToolResult) -> str | None:
            if result.code == 0 and parse_result(result.stdout) is not None and target.is_file() and target.stat().st_size > 0:
                library.update(derived.model_copy(update={"outcome": Outcome.succeeded, "stats": source.stats, "seed": source.seed, "options": source.options}))
                return None
            target.unlink(missing_ok=True)
            reason = failure_reason(result.stderr, result.code, "The script gave no result" if result.code == 0 else f"Animation failed with exit code {result.code}")
            library.update(derived.model_copy(update={"outcome": Outcome.failed, "error": JobError(code="walk_failed", message=reason)}))
            return reason

        try:
            tasks.start(entry_id, f"walk:{derived.entry_id}", "animate_rig.py", ["walk", str(library.model_path(entry_id)), str(target), body_type], finish)
        except ToolBusy:
            library.delete(derived.entry_id)
            raise ApiError(409, "entry_busy", "A tool is already running for this entry")
        return view

    return router
