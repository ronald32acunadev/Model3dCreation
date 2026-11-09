"""Library API: list, save, and download library models."""

import re
import shutil

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobState
from model3d_contracts.library import ENTRY_FILE, Entry, Library, Outcome
from model3d_engine.api.errors import ApiError
from model3d_engine.jobs.scheduler import STATS_FILE
from model3d_engine.jobs.store import JobStore
from model3d_engine.stages.protocol import MODEL_OUTPUT

ORIGINAL_MODEL = "model_original.glb"
_ENTRY_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]*")


class EntryView(Entry):
    """A library entry with the names of its files."""
    files: list[str]


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    name: str | None = None


def library_router(library: Library, store: JobStore) -> APIRouter:
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

    return router
