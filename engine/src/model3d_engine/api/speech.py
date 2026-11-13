"""Speech API: turn text into audio with a saved voice."""

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.datastructures import UploadFile

from model3d_contracts.jobs import Job, JobState

from model3d_engine.api.errors import ApiError
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import REQUEST_FILE, JobRecord, JobStore
from model3d_engine.voices import Language, Voice, VoiceKind, VoiceStore

MAX_TEXT_CHARS = 2000
MAX_DESCRIPTION_CHARS = 1000
MAX_SAMPLE_TEXT_CHARS = 500
MAX_AUDIO_BYTES = 20 * 1024 * 1024
AUDIO_SUFFIXES = (".wav", ".mp3", ".ogg", ".flac")
LANGUAGES = ("es", "en")
SPEECH_OUTPUT = "speech.wav"  # the voice stage writes it in the job folder
REFERENCE_OUTPUT = "reference.wav"  # a transcribe job's clip
TRANSCRIPT_OUTPUT = "transcript.json"
SPEAK = "speak"
DESIGN = "design"
TRANSCRIBE = "transcribe"
POLL_SECONDS = 0.2


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str
    language: Language
    text: str


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    language: Language
    sample_text: str


class DesignedVoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["designed"]
    preview_id: str


def speech_record(store: JobStore, job_id: str) -> JobRecord | None:
    """The record for job_id when it is a speech job."""
    record = store.get(job_id)
    return record if record is not None and record.kind == "speech" else None


def speech_result(record: JobRecord) -> FileResponse:
    """The finished speech job's WAV, or 409 until it is done."""
    if record.state != JobState.succeeded:
        raise ApiError(409, "job_not_finished", f"Job {record.job_id} is {record.state.value}")
    return FileResponse(record.dir / SPEECH_OUTPUT, media_type="audio/wav", filename=SPEECH_OUTPUT)


def clean_text(text: str) -> str:
    """The stripped text of a speech request, or 400 invalid_text."""
    text = text.strip()
    if not text:
        raise ApiError(400, "invalid_text", "Text must not be empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(400, "invalid_text", f"Text is limited to {MAX_TEXT_CHARS} characters")
    return text


def check_voice_ready(scheduler: Scheduler, voice_reason: Callable[[], str | None]) -> None:
    if not scheduler.voice_available:
        raise ApiError(400, "voice_unavailable", voice_reason() or "Voice generation is not available")
    if scheduler.start_error is not None:
        raise ApiError(503, "engine_not_ready", f"The engine failed to start: {scheduler.start_error.message}")
    if not scheduler.ready:
        raise ApiError(503, "engine_not_ready", "The engine is still loading its models")


def submit_speak_job(
    scheduler: Scheduler,
    store: JobStore,
    voices: VoiceStore,
    voice: Voice,
    text: str,
    language: Language,
    character_id: str | None = None,
) -> Job:
    """Queue a speech job that speaks `text` with a saved voice."""
    request = {
        "type": SPEAK,
        "text": text,
        "language": language,
        "ref_audio": str(voices.sample_path(voice.voice_id).resolve()),
        "ref_text": voice.transcript,
        "instruct": None,
    }
    if character_id is not None:
        request["character_id"] = character_id
    record = store.create_speech(request)
    scheduler.submit_speech(record)
    return record.to_job(None)


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ApiError(400, "invalid_name", "Name must not be empty")
    return cleaned


def speech_router(
    scheduler: Scheduler,
    store: JobStore,
    voices: VoiceStore,
    voice_reason: Callable[[], str | None],
    transcribe_timeout_s: float = 600.0,
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/speech-jobs", status_code=202, response_model=Job)
    async def submit_speech(body: SpeechRequest) -> Job:
        text = clean_text(body.text)
        voice = voices.get(body.voice_id)
        if voice is None:
            raise ApiError(404, "voice_not_found", f"No voice {body.voice_id}")
        check_voice_ready(scheduler, voice_reason)
        return submit_speak_job(scheduler, store, voices, voice, text, body.language)

    @router.post("/v1/voice-previews", status_code=202, response_model=Job)
    async def submit_preview(body: PreviewRequest) -> Job:
        description = body.description.strip()
        if not 1 <= len(description) <= MAX_DESCRIPTION_CHARS:
            raise ApiError(400, "invalid_description", f"Description must be 1 to {MAX_DESCRIPTION_CHARS} characters")
        text = body.sample_text.strip()
        if not 1 <= len(text) <= MAX_SAMPLE_TEXT_CHARS:
            raise ApiError(400, "invalid_text", f"Sample text must be 1 to {MAX_SAMPLE_TEXT_CHARS} characters")
        check_voice_ready(scheduler, voice_reason)
        record = store.create_speech(
            {"type": DESIGN, "text": text, "language": body.language, "ref_audio": None, "ref_text": None, "instruct": description}
        )
        scheduler.submit_speech(record)
        return record.to_job(None)

    @router.post("/v1/voices", status_code=201, response_model=Voice)
    async def create_voice(request: Request) -> Voice:
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type == "application/json":
            return await save_designed(request)
        if content_type == "multipart/form-data":
            return await save_recorded(request)
        raise ApiError(415, "unsupported_media_type", "Send application/json or multipart/form-data")

    async def save_designed(request: Request) -> Voice:
        try:
            body = DesignedVoiceRequest.model_validate(await request.json())
        except ValueError as error:
            message = error.errors()[0]["msg"] if isinstance(error, ValidationError) else "Body must be valid JSON"
            raise ApiError(400, "invalid_request", message) from None
        name = _clean_name(body.name)
        preview = speech_record(store, body.preview_id)
        design = json.loads((preview.dir / REQUEST_FILE).read_text()) if preview is not None else None
        if design is None or design["type"] != DESIGN:
            raise ApiError(404, "preview_not_found", f"No voice preview {body.preview_id}")
        if preview.state != JobState.succeeded:
            raise ApiError(409, "preview_not_finished", f"Preview {preview.job_id} is {preview.state.value}")
        sample = (preview.dir / SPEECH_OUTPUT).read_bytes()
        return voices.create(name, VoiceKind.designed, design["language"], design["text"], sample)

    async def save_recorded(request: Request) -> Voice:
        form = await request.form()
        name = _clean_name(str(form.get("name") or ""))
        language = str(form.get("language") or "")
        if language not in LANGUAGES:
            raise ApiError(400, "invalid_language", "Language must be es or en")
        audio = form.get("audio")
        if not isinstance(audio, UploadFile):
            raise ApiError(400, "invalid_audio", "Missing audio file")
        suffix = Path(audio.filename or "").suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            raise ApiError(400, "invalid_audio", f"Audio must be one of {', '.join(AUDIO_SUFFIXES)}")
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise ApiError(413, "audio_too_large", f"Recordings are limited to {MAX_AUDIO_BYTES} bytes")
        check_voice_ready(scheduler, voice_reason)
        record = store.create_transcription(language, f"reference_upload{suffix}", data)
        scheduler.submit_speech(record)
        deadline = time.monotonic() + transcribe_timeout_s
        while not record.is_final:
            if time.monotonic() > deadline:
                raise ApiError(504, "transcription_timeout", "The recording was not transcribed in time")
            await asyncio.sleep(POLL_SECONDS)
        if record.state != JobState.succeeded:
            error = record.error
            if error is not None and error.code == "invalid_audio":
                raise ApiError(400, "invalid_audio", error.message)
            raise ApiError(
                500,
                error.code if error is not None else "transcription_failed",
                error.message if error is not None else f"Transcription ended as {record.state.value}",
            )
        transcript = json.loads((record.dir / TRANSCRIPT_OUTPUT).read_text(encoding="utf-8"))["text"].strip()
        if not transcript:
            raise ApiError(400, "invalid_audio", "No speech found in the recording")
        return voices.create(name, VoiceKind.recorded, language, transcript, (record.dir / REFERENCE_OUTPUT).read_bytes())

    @router.get("/v1/speech-jobs/{job_id}/result")
    async def get_speech_result(job_id: str) -> FileResponse:
        record = speech_record(store, job_id)
        if record is None:
            raise ApiError(404, "job_not_found", f"No speech job {job_id}")
        return speech_result(record)

    return router
