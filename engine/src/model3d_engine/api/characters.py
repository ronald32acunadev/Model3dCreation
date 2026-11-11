"""Characters API: list, read and rename the characters of the library, and speak with their voices."""

import re
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict

from model3d_contracts.characters import Character, CharacterData, Characters, Phrase
from model3d_contracts.jobs import Job
from model3d_engine.api.errors import ApiError
from model3d_engine.api.speech import check_voice_ready, clean_text, submit_speak_job
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore
from model3d_engine.voices import Language, VoiceStore

_CHARACTER_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]*")


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class VoiceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_id: str


class CharacterSpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    language: Language


def characters_router(
    characters: Characters,
    voices: VoiceStore,
    store: JobStore,
    scheduler: Scheduler,
    voice_reason: Callable[[], str | None],
) -> APIRouter:
    router = APIRouter()

    def _find(character_id: str) -> Character:
        character = characters.get(character_id) if _CHARACTER_ID.fullmatch(character_id) else None
        if character is None:
            raise ApiError(404, "character_not_found", f"No character {character_id}")
        return character

    @router.get("/v1/characters", response_model=list[Character])
    async def list_characters() -> list[Character]:
        return characters.characters()

    @router.get("/v1/characters/{character_id}", response_model=Character)
    async def get_character(character_id: str) -> Character:
        return _find(character_id)

    @router.patch("/v1/characters/{character_id}", response_model=Character)
    async def rename_character(character_id: str, body: RenameRequest) -> Character:
        _find(character_id)
        try:
            return characters.rename(character_id, body.name)
        except KeyError as error:
            raise ApiError(404, "character_not_found", f"No character {character_id}") from error
        except ValueError as error:
            raise ApiError(400, "invalid_name", str(error)) from error

    @router.put("/v1/characters/{character_id}/voice", response_model=Character)
    async def link_voice(character_id: str, body: VoiceLink) -> Character:
        _find(character_id)
        if voices.get(body.voice_id) is None:
            raise ApiError(404, "voice_not_found", f"No voice {body.voice_id}")
        data = characters.data(character_id)
        characters.save_data(character_id, data.model_copy(update={"voice_id": body.voice_id}))
        return _find(character_id)

    @router.delete("/v1/characters/{character_id}/voice", status_code=204)
    async def unlink_voice(character_id: str) -> Response:
        _find(character_id)
        data = characters.data(character_id)
        characters.save_data(character_id, data.model_copy(update={"voice_id": None}))
        return Response(status_code=204)

    @router.post("/v1/characters/{character_id}/speech", status_code=202, response_model=Job)
    async def speak(character_id: str, body: CharacterSpeechRequest) -> Job:
        character = _find(character_id)
        text = clean_text(body.text)
        voice = voices.get(character.voice_id) if character.voice_id is not None else None
        if voice is None:
            raise ApiError(409, "voice_not_assigned", f"Character {character_id} has no voice")
        check_voice_ready(scheduler, voice_reason)
        return submit_speak_job(scheduler, store, voices, voice, text, body.language, character_id)

    @router.get("/v1/characters/{character_id}/phrases", response_model=list[Phrase])
    async def list_phrases(character_id: str) -> list[Phrase]:
        _find(character_id)
        return characters.phrases(character_id)

    @router.get("/v1/characters/{character_id}/phrases/{phrase_id}/audio")
    async def phrase_audio(character_id: str, phrase_id: str) -> FileResponse:
        _find(character_id)
        audio = characters.phrase_audio(character_id, phrase_id)
        if audio is None:
            raise ApiError(404, "phrase_not_found", f"No phrase {phrase_id}")
        return FileResponse(audio, media_type="audio/wav", filename=audio.name)

    @router.delete("/v1/characters/{character_id}/phrases/{phrase_id}", status_code=204)
    async def delete_phrase(character_id: str, phrase_id: str) -> Response:
        _find(character_id)
        if not characters.delete_phrase(character_id, phrase_id):
            raise ApiError(404, "phrase_not_found", f"No phrase {phrase_id}")
        return Response(status_code=204)

    return router
