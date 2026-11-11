# Voice studio

Status: US-01..US-08 approved 2026-11-09 ("Approve, all stories"), execution mode: all stories. US-09..US-12 added and approved 2026-11-11 ("Si"), including the voice-deletion assumption; execution mode for them pending. Constraint 2026-11-11 (verbatim): "quiero que todo se haga por medio de modelos de IA locales", clarified "Me refiero a la generacion de voces" — every voice step (design, cloning, speech, transcription) runs on local models on this machine; no cloud TTS or transcription service is called, and after the one-time weight download it works offline. Code authorship follows AGENTS.md as before. US-01 in progress. Branch: `feat/public-api-voice`. Depends on `docs/userstories/public-api/` (the engine API, its LAN address and API key).

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
- US-08 / AC-02: The character flow (US-09..US-12: create a character, give it a voice, ask it for phrases, export and import it) is documented as its own section, because another agent will use it.

## US-10: Characters group a model's versions

Added 2026-11-11. Request (verbatim): "los personajes que se generen serian como proyectos con toda la informacion asociada por lo que tambien podria importarse". Contents confirmed in chat ("Yes, that"): source image, every model version (base, rigged, walk, exports), the root voice (reference sample and transcript), the phrases generated with it, and name, date and generation options.

As the owner, I want each generated character to be one project that holds all its versions, so that I work with characters instead of loose library entries.

- US-10 / AC-01: A character is a root library entry (one with no `source_entry_id`) plus every entry derived from it, at any depth; its id is the root's `entry_id`. Existing libraries need no migration: characters are computed from the entries already on disk, and character data (voice link, phrases) lives in a new `character.json` and `phrases/` folder in the root entry's folder, written only when there is something to store.
- US-10 / AC-02: `GET /v1/characters` lists characters (id, name, created, source image file, version entry ids, `voice_id`, phrase count); `GET /v1/characters/{id}` returns one; `PATCH /v1/characters/{id}` with `{"name"}` renames it (the root entry's name). Unknown id, or the id of a derived entry → 404 `character_not_found`.
- US-10 / AC-03: The app's library shows one card per character, with its versions inside, instead of one card per entry; opening a version works as it does today.

## US-09: A character speaks with its own voice

Added 2026-11-11. Request (verbatim): "agregar poder generar voces o audios de voz par aun personaje es decir creamos un tono de voz raiz y al pedir que el texto sea esa voz solo se genera el texto , pero tienen que ser de manera local"; follow-up: "Creo que asociarla a un modelo 3d , pero voy a tener que tener despinible por medio del api para poder pedirle frases a otro agente".

As the owner, or another agent using the public API, I want to give a character its root voice once and then ask it for phrases with only the text, so that a character always speaks with the same voice.

- US-09 / AC-01: `PUT /v1/characters/{id}/voice` with `{"voice_id"}` links a saved voice; `GET /v1/characters/{id}` then shows `voice_id` (`null` when none); `DELETE /v1/characters/{id}/voice` removes the link. One voice per character; one voice may serve several characters.
- US-09 / AC-02: `POST /v1/characters/{id}/speech` with `{"text", "language": "es"|"en"}` answers 202 with a speech job that uses the linked voice; the job behaves like US-02 (events over the WebSocket, WAV from `GET /v1/speech-jobs/{job_id}/result`). The caller sends no voice id or voice settings.
- US-09 / AC-03: A finished character phrase is kept in the character: `GET /v1/characters/{id}/phrases` lists them (id, text, language, created), `GET /v1/characters/{id}/phrases/{phrase_id}/audio` returns the WAV, `DELETE` on the same path removes it.
- US-09 / AC-04: Unknown character → 404 `character_not_found`; unknown voice in the link → 404 `voice_not_found`; speech for a character with no voice → 409 `voice_not_assigned`; unknown phrase → 404 `phrase_not_found`; text and install errors as in US-02 / AC-03.
- US-09 / AC-05: These endpoints follow the same API key rule as the rest of the API: LAN clients need the key, localhost does not.
- US-09 / AC-06: In the app, a character shows its voice, lets the user pick or clear it, has a "Say" box that generates and plays a phrase, and lists its phrases with play and delete.
- Confirmed 2026-11-11: deleting a voice that characters use clears those links, and those characters then answer `voice_not_assigned`. Their saved phrases stay.

## US-11: Export a character

As the owner, I want to export a character as one file, so that I can move it to another machine or keep a backup.

- US-11 / AC-01: `GET /v1/characters/{id}/package` returns `<name>.m3dchar`, a zip with `manifest.json` (package format version, character name, created, generation options, versions and how they derive from each other, voice name, language and transcript, phrases) and the files: source image, every version's files including exports, the voice's reference audio, and the phrase WAVs.
- US-11 / AC-02: A character with no voice or no phrases exports without them; the manifest says so.
- US-11 / AC-03: The app has "Export character…" with a save dialog; errors show like other export errors.

## US-12: Import a character

As the owner, or another agent using the API, I want to import a `.m3dchar` file, so that the character, its voice and its phrases appear here ready to use.

- US-12 / AC-01: `POST /v1/characters/import` (multipart `package`) answers 201 with the new character; its entries, voice and phrases get new ids, keep their derivation and names, and the voice appears in the voice list linked to the character. Importing the same file twice makes two characters.
- US-12 / AC-02: A file that is not a zip, has no or an unreadable manifest, an unknown package format version, a file the manifest names but the zip lacks, or a zip path that would land outside the target folder answers 400 `invalid_package` with the reason, and nothing is written (no partial entries, voices or phrases).
- US-12 / AC-03: An exported then imported character matches the original: same names, versions, derivation, files byte for byte, voice transcript and phrase texts.
- US-12 / AC-04: The app has "Import character…" with a file dialog; the character appears in the library when done.

## Dependencies

US-01 → US-05 → (US-02, US-03, US-04) → US-10 → US-09 → US-11 → US-12 → US-06 → US-07 → US-08. US-01 needs the GPUs free (LM Studio's model unloaded). US-10 does not need the voice model and can be built before US-01 finishes.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-04 | `scripts/voice/try_voice.py` run on one card; samples in `data/voice_trial/`; owner listens | Partial, 2026-11-11, cuda:0 RTX 5070, LM Studio and Ollama unloaded, sdpa attention (no FlashAttention), bf16: VoiceDesign es 10.3 s audio in 13.6 s, en 5.8 s in 7.4 s, peak reserved 4.45 GB; designed voice reused through Base (es 7.4 s in 9.1 s, en 8.3 s in 10.1 s), peak reserved 4.92 GB; load 6.8 s / 6.5 s. Real-time factor 1.2–1.3 (slower than real time). Owner listened to the four samples, 2026-11-11: "Sound natural" (AC-04 for designed voices). Pending: clone and Whisper from the owner's recording (AC-01 clone part, AC-03). |
| US-02 / AC-01..AC-04 | `engine/tests/test_speech.py` with a fake voice stage (WAV joined from pieces, errors, health); one real job | Pending |
| US-03 / AC-01..AC-03 | `engine/tests/test_voices.py` (preview then save, errors); one real designed voice | Pending |
| US-04 / AC-01..AC-03 | `engine/tests/test_voices.py` with a fake transcriber (limits, bad file); client test for the recorder view model; one real recording in the app | Pending |
| US-05 / AC-01..AC-03 | `engine/tests/test_voices.py` (list, sample, rename, delete, 404) | Pending |
| US-06 / AC-01..AC-03 | client tests for the voice view model and window switch; one manual pass in the running app | Pending |
| US-07 / AC-01..AC-02 | client setup recipe tests; one install into a fresh folder | Pending |
| US-08 / AC-01..AC-02 | curl examples run against the real engine | Pending |
| US-10 / AC-01..AC-02 | `engine/tests/test_characters.py` (grouping at depth, existing library without `character.json`, rename, 404 for derived id); existing library tests still pass | Pending |
| US-10 / AC-03 | client test for the character cards view model; one manual pass in the running app | Pending |
| US-09 / AC-01..AC-05 | `engine/tests/test_character_voice.py` with a fake voice stage (link, unlink, speak, phrases list/audio/delete, 404/409, LAN key, voice deletion clears links); one real phrase via curl from another machine | Pending |
| US-09 / AC-06 | client test for the voice picker, "Say" box and phrase list; one manual pass in the running app | Pending |
| US-11 / AC-01..AC-02 | `engine/tests/test_character_package.py` (manifest contents, files present, character without voice) | Pending |
| US-11 / AC-03, US-12 / AC-04 | client tests for export/import actions; one manual export and import in the running app | Pending |
| US-12 / AC-01..AC-03 | `engine/tests/test_character_package.py` (round trip byte for byte, twice-imported, each `invalid_package` case incl. path traversal, nothing written after rejection) | Pending |
