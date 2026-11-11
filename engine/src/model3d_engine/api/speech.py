"""Speech API: turn text into audio with a saved voice."""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from model3d_contracts.jobs import Job, JobState

from model3d_engine.api.errors import ApiError
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobRecord, JobStore
from model3d_engine.voices import Language, VoiceStore

MAX_TEXT_CHARS = 2000
SPEECH_OUTPUT = "speech.wav"  # the voice stage writes it in the job folder
SPEAK = "speak"


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str
    language: Language
    text: str


def speech_record(store: JobStore, job_id: str) -> JobRecord | None:
    """The record for job_id when it is a speech job."""
    record = store.get(job_id)
    return record if record is not None and record.kind == "speech" else None


def speech_result(record: JobRecord) -> FileResponse:
    """The finished speech job's WAV, or 409 until it is done."""
    if record.state != JobState.succeeded:
        raise ApiError(409, "job_not_finished", f"Job {record.job_id} is {record.state.value}")
    return FileResponse(record.dir / SPEECH_OUTPUT, media_type="audio/wav", filename=SPEECH_OUTPUT)


def speech_router(
    scheduler: Scheduler, store: JobStore, voices: VoiceStore, voice_reason: Callable[[], str | None]
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/speech-jobs", status_code=202, response_model=Job)
    async def submit_speech(body: SpeechRequest) -> Job:
        text = body.text.strip()
        if not text:
            raise ApiError(400, "invalid_text", "Text must not be empty")
        if len(text) > MAX_TEXT_CHARS:
            raise ApiError(400, "invalid_text", f"Text is limited to {MAX_TEXT_CHARS} characters")
        voice = voices.get(body.voice_id)
        if voice is None:
            raise ApiError(404, "voice_not_found", f"No voice {body.voice_id}")
        if not scheduler.voice_available:
            raise ApiError(400, "voice_unavailable", voice_reason() or "Voice generation is not available")
        if scheduler.start_error is not None:
            raise ApiError(503, "engine_not_ready", f"The engine failed to start: {scheduler.start_error.message}")
        if not scheduler.ready:
            raise ApiError(503, "engine_not_ready", "The engine is still loading its models")
        record = store.create_speech(
            {
                "type": SPEAK,
                "text": text,
                "language": body.language,
                "ref_audio": str(voices.sample_path(voice.voice_id).resolve()),
                "ref_text": voice.transcript,
                "instruct": None,
            }
        )
        scheduler.submit_speech(record)
        return record.to_job(None)

    @router.get("/v1/speech-jobs/{job_id}/result")
    async def get_speech_result(job_id: str) -> FileResponse:
        record = speech_record(store, job_id)
        if record is None:
            raise ApiError(404, "job_not_found", f"No speech job {job_id}")
        return speech_result(record)

    return router
