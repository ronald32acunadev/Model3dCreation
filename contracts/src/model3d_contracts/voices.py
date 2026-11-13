"""The saved-voice model shared by the engine and its clients."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

Language = Literal["es", "en"]


class VoiceKind(str, Enum):
    designed = "designed"
    recorded = "recorded"


class Voice(BaseModel):
    voice_id: str
    name: str
    kind: VoiceKind
    language: Language
    created_at: datetime
    transcript: str  # the text spoken in the sample; cloning uses it later
