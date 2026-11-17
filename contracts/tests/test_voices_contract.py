import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from model3d_contracts.voices import Voice, VoiceKind

STORED = {
    "voice_id": "20261101T120000-a1b2c3",
    "name": "Narrator",
    "kind": "designed",
    "language": "es",
    "created_at": "2026-11-01T12:00:00Z",
    "transcript": "Hola, mundo",
}


def test_voice_parses_the_json_the_engine_writes():
    voice = Voice.model_validate_json(json.dumps(STORED))
    assert voice.voice_id == "20261101T120000-a1b2c3"
    assert voice.name == "Narrator"
    assert voice.kind is VoiceKind.designed
    assert voice.language == "es"
    assert voice.created_at == datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    assert voice.transcript == "Hola, mundo"


def test_voice_round_trips_through_json():
    voice = Voice.model_validate(STORED)
    assert Voice.model_validate_json(voice.model_dump_json()) == voice
    assert json.loads(voice.model_dump_json())["kind"] == "designed"


def test_voice_rejects_an_unknown_language():
    with pytest.raises(ValidationError):
        Voice.model_validate({**STORED, "language": "fr"})
