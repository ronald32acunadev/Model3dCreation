"""Qwen3-TTS worker: runs under vendor/Qwen3-TTS/.venv and speaks JSON lines on stdout.

VoiceStage (model3d_engine.stages.voice) starts this script. It must not import
model3d_engine, model3d_contracts or pydantic, because it runs in another interpreter.
"""

import gc
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# Protocol lines go to a private copy of the real stdout; library prints go to stderr.
PROTO = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)

DESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
BASE = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
WHISPER = "openai/whisper-large-v3-turbo"
LANGUAGES = {"es": "Spanish", "en": "English"}
MODES = ("speak", "design", "transcribe")
CLIP_SECONDS = (3, 60)
WHISPER_RATE = 16000
OUTPUT_RATE = 24000
PAUSE_SECONDS = 0.3
SENTENCE = re.compile(r"[^.!?…]+[.!?…]*")


def send(message: dict) -> None:
    PROTO.write(json.dumps(message) + "\n")
    PROTO.flush()


def exit_with_parent(parent: int) -> None:
    """Die with the engine's proxy process, so unloading the stage frees the GPU."""
    while True:
        time.sleep(1)
        if os.getppid() != parent:
            os._exit(1)


try:
    import numpy as np
    import soundfile as sf
    import librosa
    import torch
    from huggingface_hub import snapshot_download
    from transformers import pipeline
    from qwen_tts import Qwen3TTSModel
except Exception as error:
    send({"type": "error", "code": "worker_start_failed", "message": f"{type(error).__name__}: {error}"})
    os._exit(1)


class InvalidRequest(Exception):
    """A run message that cannot be synthesized as sent."""


class InvalidAudio(Exception):
    """A reference clip that is unreadable or outside the allowed length."""


class Models:
    """Keeps at most one model (a Qwen3-TTS model or Whisper) on the GPU, loaded from its local snapshot on first use."""

    def __init__(self):
        self.repo = None
        self.model = None

    def get(self, repo: str):
        if self.repo != repo:
            self.drop()
            # transformers queries the Hub for a repo id even offline, so load the cached path.
            path = snapshot_download(repo, local_files_only=True)
            if repo == WHISPER:
                self.model = pipeline(
                    "automatic-speech-recognition", model=path, torch_dtype=torch.float16, device="cuda:0"
                )
            else:
                self.model = Qwen3TTSModel.from_pretrained(
                    path, device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa"
                )
            self.repo = repo
        return self.model

    def drop(self) -> None:
        self.model = None
        self.repo = None
        gc.collect()
        torch.cuda.empty_cache()


def split_sentences(text: str) -> list[str]:
    return [piece.strip() for piece in SENTENCE.findall(text) if piece.strip()]


def parse_request(message: dict) -> dict:
    mode = message.get("mode")
    if mode not in MODES:
        raise InvalidRequest("mode must be speak or design")
    text = message.get("text")
    if not isinstance(text, str):
        raise InvalidRequest("text must be a string")
    sentences = split_sentences(text)
    if not sentences and mode != "transcribe":
        raise InvalidRequest("text has no sentences")
    language = message.get("language")
    if not isinstance(language, str) or language not in LANGUAGES:
        raise InvalidRequest("language must be es or en")
    output = message.get("output")
    if not isinstance(output, str):
        raise InvalidRequest("output must be a path")
    request = {"mode": mode, "sentences": sentences, "language": LANGUAGES[language], "output": output}
    if mode == "transcribe":
        ref_audio = message.get("ref_audio")
        transcript = message.get("transcript")
        if not isinstance(ref_audio, str) or not Path(ref_audio).is_file():
            raise InvalidRequest(f"reference audio is missing: {ref_audio}")
        if not isinstance(transcript, str):
            raise InvalidRequest("transcript must be a path")
        request.update(ref_audio=ref_audio, transcript=transcript)
    elif mode == "speak":
        ref_audio = message.get("ref_audio")
        ref_text = message.get("ref_text")
        if not isinstance(ref_audio, str) or not Path(ref_audio).is_file():
            raise InvalidRequest(f"reference audio is missing: {ref_audio}")
        if not isinstance(ref_text, str) or not ref_text.strip():
            raise InvalidRequest("ref_text must be the transcript of the reference audio")
        request.update(ref_audio=ref_audio, ref_text=ref_text)
    else:
        instruct = message.get("instruct")
        if not isinstance(instruct, str) or not instruct.strip():
            raise InvalidRequest("instruct must describe the voice")
        request["instruct"] = instruct
    return request


def transcribe(models: Models, request: dict) -> dict:
    """Transcribe the clip with Whisper and save it resampled to 24 kHz mono next to its transcript."""
    try:
        audio, _ = librosa.load(request["ref_audio"], sr=WHISPER_RATE, mono=True)
    except Exception:
        raise InvalidAudio("Unreadable audio file") from None
    seconds = len(audio) / WHISPER_RATE
    low, high = CLIP_SECONDS
    if not low <= seconds <= high:
        raise InvalidAudio(f"Recordings must be {low} to {high} seconds long, this one is {seconds:.1f} s")
    language = request["language"].lower()
    asr = models.get(WHISPER)
    text = asr(
        {"raw": audio.astype(np.float32), "sampling_rate": WHISPER_RATE},
        generate_kwargs={"language": language, "task": "transcribe"},
        return_timestamps=True,
    )["text"]
    clip = librosa.resample(audio, orig_sr=WHISPER_RATE, target_sr=OUTPUT_RATE)
    sf.write(request["output"], clip.astype(np.float32), OUTPUT_RATE)
    Path(request["transcript"]).write_text(json.dumps({"text": text.strip()}), encoding="utf-8")
    return {"type": "done", "audio_seconds": round(seconds, 3), "sample_rate": OUTPUT_RATE}


def run_job(models: Models, message: dict) -> dict:
    request = parse_request(message)
    if request["mode"] == "transcribe":
        return transcribe(models, request)
    sentences = request["sentences"]
    language = request["language"]
    if request["mode"] == "speak":
        model = models.get(BASE)
        prompt = model.create_voice_clone_prompt(
            ref_audio=request["ref_audio"], ref_text=request["ref_text"], x_vector_only_mode=False
        )

        def generate(sentence: str):
            return model.generate_voice_clone(text=sentence, language=language, voice_clone_prompt=prompt)

    else:
        model = models.get(DESIGN)

        def generate(sentence: str):
            return model.generate_voice_design(text=sentence, instruct=request["instruct"], language=language)

    clips = []
    sample_rate = None
    for index, sentence in enumerate(sentences, start=1):
        wavs, sample_rate = generate(sentence)
        clips.append(np.asarray(wavs[0], dtype=np.float32))
        send({"type": "progress", "value": index / len(sentences), "message": f"Sentence {index} of {len(sentences)}"})
    pause = np.zeros(int(PAUSE_SECONDS * sample_rate), dtype=np.float32)
    pieces = []
    for index, clip in enumerate(clips):
        if index:
            pieces.append(pause)
        pieces.append(clip)
    audio = np.concatenate(pieces)
    sf.write(request["output"], audio, sample_rate)
    return {"type": "done", "audio_seconds": round(len(audio) / sample_rate, 3), "sample_rate": sample_rate}


def main() -> None:
    threading.Thread(target=exit_with_parent, args=(os.getppid(),), daemon=True).start()
    models = Models()
    send({"type": "ready"})
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("type") == "stop":
            os._exit(0)
        if message.get("type") != "run":
            continue
        try:
            send(run_job(models, message))
        except InvalidRequest as error:
            send({"type": "error", "code": "invalid_request", "message": str(error)})
        except InvalidAudio as error:
            send({"type": "error", "code": "invalid_audio", "message": str(error)})
        except torch.OutOfMemoryError as error:
            models.drop()
            send({"type": "error", "code": "gpu_out_of_memory", "message": f"Qwen3-TTS ran out of VRAM: {error}"})
        except Exception as error:
            send({"type": "error", "code": "speech_failed", "message": str(error)})
    os._exit(0)


if __name__ == "__main__":
    main()
