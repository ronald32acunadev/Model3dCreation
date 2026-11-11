"""Characters API: list, read and rename the characters of the library."""

import re

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from model3d_contracts.characters import Character, Characters
from model3d_engine.api.errors import ApiError

_CHARACTER_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]*")


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


def characters_router(characters: Characters) -> APIRouter:
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

    return router
