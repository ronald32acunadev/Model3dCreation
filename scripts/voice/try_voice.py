"""Trial run of the Qwen3-TTS voice pipeline (US-01 of docs/userstories/voice-studio).

Run from any directory with the Qwen3-TTS environment:

    vendor/Qwen3-TTS/.venv/bin/python scripts/voice/try_voice.py
    vendor/Qwen3-TTS/.venv/bin/python scripts/voice/try_voice.py --skip-design --ref es:data/ref_es.wav --ref en:data/ref_en.wav

Relative --out and --ref paths are resolved against the repository root.
"""

import argparse
import gc
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HOME", str(REPO / "data/hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
import transformers  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from qwen_tts import Qwen3TTSModel  # noqa: E402
from transformers import pipeline  # noqa: E402

VOICE_DESIGN_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
BASE_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
WHISPER_REPO = "openai/whisper-large-v3-turbo"
WHISPER_SR = 16000
MIB = 1024 * 1024
MIN_REF_SECONDS = 3.0
MAX_REF_SECONDS = 60.0
TYPICAL_REF_SECONDS = (10.0, 20.0)
DEFAULT_OUT = REPO / "data/voice_trial"

LANG_NAMES = {"es": "Spanish", "en": "English"}
WHISPER_LANG = {"es": "spanish", "en": "english"}

DESIGN_CASES = [
    {
        "name": "es_narrator",
        "language": "Spanish",
        "instruct": (
            "A warm, deep, middle-aged male narrator voice, calm and expressive, "
            "speaking clearly at a measured pace."
        ),
        "text": (
            "Hace muchos años, en un pequeño pueblo de montaña, vivía un relojero "
            "que nunca llegaba tarde. Cada mañana abría su taller con la misma calma."
        ),
    },
    {
        "name": "en_young_female",
        "language": "English",
        "instruct": (
            "A bright, young female voice with a lively, friendly tone "
            "and an energetic, upbeat delivery."
        ),
        "text": (
            "Hey there! I just finished the new prototype, and honestly, "
            "I can't wait for you to hear what it sounds like."
        ),
    },
]

CLONE_TEXT = {
    "es": (
        "Esta es una prueba de voz para comprobar si el modelo reproduce el timbre "
        "y el ritmo de la grabación original con naturalidad."
    ),
    "en": (
        "This is a short test to check whether the cloned voice keeps the same tone, "
        "pace and character as the original recording."
    ),
}


@dataclass
class Reference:
    name: str
    language: str
    path: Path
    seconds: float
    audio: np.ndarray
    transcript: str = ""


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Try VoiceDesign and Base voice cloning on one GPU.")
    parser.add_argument("--device", default="cuda:0", help="CUDA device to run on (default: cuda:0)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        metavar="LANG:PATH",
        help="reference recording of 10-20 s; LANG is es or en (repeatable)",
    )
    parser.add_argument("--skip-design", action="store_true", help="skip the VoiceDesign phase")
    return parser


def resolve_device(name: str) -> torch.device | None:
    try:
        device = torch.device(name)
    except RuntimeError:
        return None
    if device.type != "cuda" or not torch.cuda.is_available():
        return None
    index = 0 if device.index is None else device.index
    if index >= torch.cuda.device_count():
        return None
    return torch.device("cuda", index)


def load_references(specs: list[str], parser: argparse.ArgumentParser) -> list[Reference]:
    references = []
    for spec in specs:
        lang, _, raw_path = spec.partition(":")
        if lang not in LANG_NAMES or not raw_path:
            parser.error(f"--ref expects LANG:PATH with LANG es or en, got {spec!r}")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = REPO / path
        if not path.is_file():
            parser.error(f"--ref file not found: {path}")
        audio, _ = librosa.load(str(path), sr=WHISPER_SR, mono=True)
        seconds = len(audio) / WHISPER_SR
        if not MIN_REF_SECONDS <= seconds <= MAX_REF_SECONDS:
            warn(
                f"skipping {path}: {seconds:.1f} s is outside "
                f"{MIN_REF_SECONDS:g}-{MAX_REF_SECONDS:g} s"
            )
            continue
        low, high = TYPICAL_REF_SECONDS
        if not low <= seconds <= high:
            warn(f"{path} is {seconds:.1f} s; {low:g}-{high:g} s gives the best clones")
        references.append(Reference(path.stem, lang, path, seconds, audio))
    return references


def reset_peak(device: torch.device) -> None:
    torch.cuda.synchronize(device)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(device)


def timed(device: torch.device, generate):
    torch.cuda.synchronize(device)
    started = time.perf_counter()
    output = generate()
    torch.cuda.synchronize(device)
    return output, time.perf_counter() - started


def peak_fields(device: torch.device) -> dict:
    return {
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated(device) / MIB, 1),
        "peak_reserved_mib": round(torch.cuda.max_memory_reserved(device) / MIB, 1),
    }


def generation_record(
    phase: str,
    name: str,
    language: str,
    seconds: float,
    audio: np.ndarray,
    sample_rate: int,
    device: torch.device,
    file_name: str,
) -> dict:
    return {
        "phase": phase,
        "name": name,
        "language": language,
        "seconds": round(seconds, 3),
        "audio_seconds": round(len(audio) / sample_rate, 3),
        **peak_fields(device),
        "file": file_name,
    }


def local_snapshot(repo: str) -> str:
    """Path of the downloaded weights; transformers 4.57.3 queries the Hub for any repo id, even offline."""
    return snapshot_download(repo, local_files_only=True)


def load_qwen(repo: str, device_name: str) -> tuple[Qwen3TTSModel, float]:
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        local_snapshot(repo),
        device_map=device_name,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    return model, time.perf_counter() - started


def run_design(device: torch.device, out_dir: Path, results: list[dict]) -> tuple[dict | None, float]:
    model, load_seconds = load_qwen(VOICE_DESIGN_REPO, str(device))
    design_sample = None
    try:
        for case in DESIGN_CASES:
            reset_peak(device)
            (wavs, sample_rate), seconds = timed(
                device,
                lambda: model.generate_voice_design(
                    text=case["text"],
                    instruct=case["instruct"],
                    language=case["language"],
                    non_streaming_mode=True,
                ),
            )
            audio = wavs[0]
            file_name = f"design_{case['name']}.wav"
            sf.write(out_dir / file_name, audio, sample_rate)
            results.append(
                generation_record(
                    "design", case["name"], case["language"], seconds,
                    audio, sample_rate, device, file_name,
                )
            )
            if case["language"] == "Spanish":
                design_sample = {
                    "name": case["name"],
                    "audio": audio,
                    "sample_rate": sample_rate,
                    "text": case["text"],
                }
    finally:
        del model
        gc.collect()
        torch.cuda.empty_cache()
    return design_sample, load_seconds


def run_transcription(device: torch.device, references: list[Reference], results: list[dict]) -> float:
    started = time.perf_counter()
    asr = pipeline(
        "automatic-speech-recognition",
        model=local_snapshot(WHISPER_REPO),
        torch_dtype=torch.float16,
        device=str(device),
    )
    load_seconds = time.perf_counter() - started
    try:
        for ref in references:
            reset_peak(device)
            output, seconds = timed(
                device,
                lambda: asr(
                    {"raw": ref.audio, "sampling_rate": WHISPER_SR},
                    generate_kwargs={"language": WHISPER_LANG[ref.language], "task": "transcribe"},
                    return_timestamps=True,
                ),
            )
            ref.transcript = output["text"].strip()
            results.append(
                {
                    "phase": "transcribe",
                    "name": ref.name,
                    "language": LANG_NAMES[ref.language],
                    "seconds": round(seconds, 3),
                    "ref_audio_seconds": round(ref.seconds, 3),
                    "transcript": ref.transcript,
                    **peak_fields(device),
                }
            )
    finally:
        del asr
        gc.collect()
        torch.cuda.empty_cache()
    return load_seconds


def generate_clones(
    model: Qwen3TTSModel,
    device: torch.device,
    prompt: list,
    phase: str,
    name: str,
    file_prefix: str,
    out_dir: Path,
    results: list[dict],
) -> None:
    for lang in ("es", "en"):
        text = CLONE_TEXT[lang]
        reset_peak(device)
        (wavs, sample_rate), seconds = timed(
            device,
            lambda: model.generate_voice_clone(
                text=[text],
                language=[LANG_NAMES[lang]],
                voice_clone_prompt=prompt,
            ),
        )
        audio = wavs[0]
        file_name = f"{file_prefix}_{lang}.wav"
        sf.write(out_dir / file_name, audio, sample_rate)
        results.append(
            generation_record(
                phase, name, LANG_NAMES[lang], seconds,
                audio, sample_rate, device, file_name,
            )
        )


def run_base(
    device: torch.device,
    design_sample: dict | None,
    references: list[Reference],
    out_dir: Path,
    results: list[dict],
) -> float:
    model, load_seconds = load_qwen(BASE_REPO, str(device))
    try:
        if design_sample is not None:
            prompt = model.create_voice_clone_prompt(
                ref_audio=(design_sample["audio"], design_sample["sample_rate"]),
                ref_text=design_sample["text"],
                x_vector_only_mode=False,
            )
            generate_clones(
                model, device, prompt, "designed_clone", design_sample["name"],
                "designed_clone", out_dir, results,
            )
        for ref in references:
            prompt = model.create_voice_clone_prompt(
                ref_audio=str(ref.path),
                ref_text=ref.transcript,
                x_vector_only_mode=False,
            )
            generate_clones(
                model, device, prompt, "clone", ref.name,
                f"clone_{ref.name}", out_dir, results,
            )
    finally:
        del model
        gc.collect()
        torch.cuda.empty_cache()
    return load_seconds


def print_table(results: list[dict]) -> None:
    print(
        f"{'phase':<16}{'name':<18}{'language':<10}{'gen_s':>8}"
        f"{'audio_s':>9}{'rtf':>7}{'peak_res_MiB':>14}"
    )
    for row in results:
        audio = row.get("audio_seconds", row.get("ref_audio_seconds"))
        rtf = row["seconds"] / audio if audio else float("nan")
        print(
            f"{row['phase']:<16}{row['name']:<18}{row['language']:<10}"
            f"{row['seconds']:>8.2f}{audio or 0:>9.2f}{rtf:>7.2f}"
            f"{row['peak_reserved_mib']:>14.1f}"
        )
    for row in results:
        if row["phase"] == "transcribe":
            print(f"transcript {row['name']} ({row['language']}): {row['transcript']}")


def write_summary(out_dir: Path, device: torch.device, load_seconds: dict, results: list[dict]) -> Path:
    summary = {
        "device": str(device),
        "gpu_name": torch.cuda.get_device_name(device),
        "model_load_seconds": {key: round(value, 3) for key, value in load_seconds.items()},
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
        "results": results,
    }
    path = out_dir / "results.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.skip_design and not args.ref:
        print("nothing to do")
        return 0

    device = resolve_device(args.device)
    if device is None:
        print(f"error: {args.device!r} is not an available CUDA device", file=sys.stderr)
        return 2

    out_dir = args.out if args.out.is_absolute() else REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    references = load_references(args.ref, parser)
    results: list[dict] = []
    load_seconds: dict[str, float] = {}

    design_sample = None
    if not args.skip_design:
        design_sample, load_seconds["voice_design"] = run_design(device, out_dir, results)
    if references:
        load_seconds["whisper"] = run_transcription(device, references, results)
    if design_sample is not None or references:
        load_seconds["base"] = run_base(device, design_sample, references, out_dir, results)

    print_table(results)
    summary_path = write_summary(out_dir, device, load_seconds, results)
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
