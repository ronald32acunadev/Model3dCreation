# Voice studio

Status: approved 2026-11-09 ("Approve, all stories"), execution mode: all stories. US-01 in progress. Branch: `feat/public-api-voice`. Depends on `docs/userstories/public-api/` (the engine API, its LAN address and API key).

## Context

Request (verbatim, 2026-11-08): "Quiero añadir un menu aparte de la generacion de modelos 3d sobre la misma app , para mi proyecto en el que hemos estado trabajando quiero tener una seccion para poder generar archivos de audio basados en una tonalidad de voz especificada por un prompt o ya sea entrenarse mediante un audio y utilizar esto para generar audios basados en esa voz , de igual manera quiero sea local y no quiero tener boces roboticas".

Decisions in chat:

- Languages: "Spanish and English".
- Cloning: "Yo mediante un microfono mando habla y se usa ese audio como base apra hacer otros audios" — instant cloning from a recording, no training run.
- Design approved 2026-11-09 ("Yes, write backlog"): Qwen3-TTS runs inside the engine as a scheduler stage; voices are saved; the app gets a "Voice" section separate from 3D models; the same features go in the public API.

Model facts (Qwen3-TTS model card on Hugging Face, checked 2026-11-08): Apache-2.0; 10 languages including Spanish and English; `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` makes a voice from a natural-language description (`generate_voice_design(text, language, instruct)`); `Qwen/Qwen3-TTS-12Hz-1.7B-Base` clones from a reference clip (`generate_voice_clone(text, language, ref_audio, ref_text)`), and needs the clip's transcript; Python package `qwen-tts`. The card gives no VRAM figure.

Assumptions (to confirm at approval):

- The reference transcript comes from a local Whisper model, so the user only records.
- Output is 24 kHz mono WAV (the model's native rate); no MP3.
- A designed voice is saved as its generated sample plus that sample's text, so it is reused through the clone model and sounds the same every time.

Out of scope: fine-tuning on long recordings, real-time streaming playback, other languages, music, voice conversion of an existing recording.

## US-01: The voice models run on this machine

As the owner, I want proof that the voice models run on one RTX 5070 next to the 3D stages, so that the rest of the feature is built on a working base.

- US-01 / AC-01: In `vendor/Qwen3-TTS/.venv` (its own venv, like TRELLIS.2), a script generates one Spanish and one English sentence with the VoiceDesign model and one with the Base model cloning a 10–20 s recording, on one card, without FlashAttention if it does not build for this GPU.
- US-01 / AC-02: Peak VRAM and time per sentence are measured and recorded here, with LM Studio and Ollama unloaded.
- US-01 / AC-03: Whisper transcribes the same recording locally in Spanish and in English; the transcript is recorded.
- US-01 / AC-04: The owner listens to the samples and confirms they do not sound robotic; if not, the story stops here for a model decision.

## US-02: Generate speech with a saved voice

As an API client or app user, I want to turn text into speech with a saved voice, so that I get audio files in that voice.

- US-02 / AC-01: `POST /v1/speech-jobs` with `{"voice_id", "language": "es"|"en", "text"}` answers 202 with a job; the scheduler runs it on one card under the one-task-per-card rule, and progress events arrive over the job's WebSocket.
- US-02 / AC-02: `GET /v1/speech-jobs/{id}/result` returns a WAV; text longer than one sentence is split at sentence ends and the pieces are joined with short pauses.
- US-02 / AC-03: Unknown voice → 404 `voice_not_found`; empty text or text over a documented limit → 400 `invalid_text`; voice stage not installed → 400 `voice_unavailable` with the reason.
- US-02 / AC-04: `/v1/health` reports whether voice generation is available and, if not, why.

## US-03: Design a voice from a description

As a user, I want to describe a voice in words, hear a sample, and keep it if I like it, so that I can create voices without recording anyone.

- US-03 / AC-01: `POST /v1/voice-previews` with `{"description", "language", "sample_text"}` runs the VoiceDesign model and returns a WAV sample (as a job with a result, like US-02).
- US-03 / AC-02: `POST /v1/voices` with `{"name", "kind": "designed", "preview_id"}` saves that sample and its text as a voice; later speech with it uses the clone model on that sample.
- US-03 / AC-03: An empty description or name answers 400 with a code naming the field.

## US-04: Record a voice

As a user, I want to record my voice with the microphone (or upload a file) and save it, so that generated speech sounds like me.

- US-04 / AC-01: `POST /v1/voices` (multipart: `name`, `language`, `audio` WAV/MP3/OGG/FLAC) transcribes the clip with Whisper and saves the clip and transcript as a voice; the answer includes the transcript.
- US-04 / AC-02: A clip shorter than 3 s or longer than 60 s answers 400 `invalid_audio` with the limits; an unreadable file answers 400 `invalid_audio`.
- US-04 / AC-03: The app records from the default microphone with a level meter and a 30 s limit, plays the take back, and lets the user re-record before saving.

## US-05: Manage saved voices

As a user, I want to list, play, rename and delete my voices, so that I keep my set tidy.

- US-05 / AC-01: `GET /v1/voices` lists voices (id, name, kind, language, created, transcript); `GET /v1/voices/{id}/sample` returns its reference audio.
- US-05 / AC-02: `PATCH /v1/voices/{id}` renames; `DELETE /v1/voices/{id}` removes the folder; unknown ids answer 404 `voice_not_found`.
- US-05 / AC-03: Voices live in `$MODEL3D_HOME/voices/<id>/`, so the app and the API see the same set.

## US-06: A "Voice" section in the app

As a user, I want a Voice section beside 3D models, so that I make audio in the same app.

- US-06 / AC-01: The main window switches between "3D models" and "Voice"; the Voice section has: my voices (list, play, rename, delete), create a voice (Describe / Record), and generate (voice, language, text, Generate, play, Save as WAV).
- US-06 / AC-02: Progress, errors and the "not installed" reason show as they do for 3D jobs; every string goes through `tr()` with its Spanish translation.
- US-06 / AC-03: Generated clips are kept in a per-voice history in the section until deleted.

## US-07: Install the voice stage from the wizard

As a user, I want the setup wizard to install the voice stage, so that I do not run commands by hand.

- US-07 / AC-01: The recipe gains steps for the Qwen3-TTS clone and venv, the VoiceDesign and Base weights, and the Whisper weights, with resume and repair like the other steps.
- US-07 / AC-02: An install without the voice stage still works; the Voice section then shows "not installed" and a button that opens the wizard.

## US-08: Documentation

As a developer of another project, I want the voice endpoints documented, so that I can use them through the public API.

- US-08 / AC-01: `docs/api.md` (from the public-api feature) gains the voice endpoints with a curl example each, run against a real engine.

## Dependencies

US-01 → US-05 → (US-02, US-03, US-04) → US-06 → US-07 → US-08. US-01 needs the GPUs free (LM Studio's model unloaded).

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-04 | `scripts/voice/try_voice.py` run on one card; samples in `data/voice_trial/`; owner listens | Pending |
| US-02 / AC-01..AC-04 | `engine/tests/test_speech.py` with a fake voice stage (WAV joined from pieces, errors, health); one real job | Pending |
| US-03 / AC-01..AC-03 | `engine/tests/test_voices.py` (preview then save, errors); one real designed voice | Pending |
| US-04 / AC-01..AC-03 | `engine/tests/test_voices.py` with a fake transcriber (limits, bad file); client test for the recorder view model; one real recording in the app | Pending |
| US-05 / AC-01..AC-03 | `engine/tests/test_voices.py` (list, sample, rename, delete, 404) | Pending |
| US-06 / AC-01..AC-03 | client tests for the voice view model and window switch; one manual pass in the running app | Pending |
| US-07 / AC-01..AC-02 | client setup recipe tests; one install into a fresh folder | Pending |
| US-08 / AC-01 | curl examples run against the real engine | Pending |
