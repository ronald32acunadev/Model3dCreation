"""Voices API: list, play, rename and delete saved voices."""

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict

from model3d_contracts.characters import Characters
from model3d_engine.api.errors import ApiError
from model3d_engine.voices import SAMPLE_FILE, VOICE_ID, Voice, VoiceStore


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


def voices_router(store: VoiceStore, characters: Characters) -> APIRouter:
    router = APIRouter()

    def _find(voice_id: str) -> Voice:
        if VOICE_ID.fullmatch(voice_id) is None:
            raise ApiError(404, "voice_not_found", f"No voice {voice_id}")
        voice = store.get(voice_id)
        if voice is None:
            raise ApiError(404, "voice_not_found", f"No voice {voice_id}")
        return voice

    @router.get("/v1/voices", response_model=list[Voice])
    async def list_voices() -> list[Voice]:
        return store.voices()

    @router.get("/v1/voices/{voice_id}", response_model=Voice)
    async def get_voice(voice_id: str) -> Voice:
        return _find(voice_id)

    @router.get("/v1/voices/{voice_id}/sample")
    async def get_sample(voice_id: str) -> FileResponse:
        _find(voice_id)
        return FileResponse(store.sample_path(voice_id), media_type="audio/wav", filename=SAMPLE_FILE)

    @router.patch("/v1/voices/{voice_id}", response_model=Voice)
    async def rename_voice(voice_id: str, body: RenameRequest) -> Voice:
        _find(voice_id)
        try:
            return store.rename(voice_id, body.name)
        except ValueError:
            raise ApiError(400, "invalid_name", "Name must not be empty")

    @router.delete("/v1/voices/{voice_id}", status_code=204)
    async def delete_voice(voice_id: str) -> Response:
        _find(voice_id)
        characters.clear_voice(voice_id)
        store.delete(voice_id)
        return Response(status_code=204)

    return router
