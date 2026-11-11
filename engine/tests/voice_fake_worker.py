"""Stdlib-only stand-in for voice_worker.py, steered by FAKE_VOICE_MODE (tests only)."""

import json
import os
import sys
import wave

MODE = os.environ.get("FAKE_VOICE_MODE", "ok")
SAMPLE_RATE = 24000


def send(message: dict) -> None:
    print(json.dumps(message), flush=True)


def write_silence(path: str) -> None:
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * (SAMPLE_RATE // 2))


def main() -> None:
    print("[qwen] loading the speech stack", flush=True)
    if MODE == "fail_load":
        send({"type": "error", "code": "worker_start_failed", "message": "model file busy"})
        sys.exit(1)
    send({"type": "ready"})
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "stop":
            sys.exit(0)
        if message["type"] != "run":
            continue
        request = dict(
            message,
            cuda_visible_devices=os.environ.get("CUDA_VISIBLE_DEVICES"),
            hf_home_offline=os.environ.get("HF_HUB_OFFLINE"),
        )
        with open(message["output"] + ".request.json", "w") as handle:
            json.dump(request, handle)
        if message["mode"] == "transcribe":
            with open(message["ref_audio"], "rb") as clip:
                short = b"short" in clip.read()
            if short:
                send({"type": "error", "code": "invalid_audio", "message": "Recordings must be 3 to 60 seconds long"})
                continue
            write_silence(message["output"])
            with open(message["transcript"], "w") as handle:
                json.dump({"text": "Hola, soy yo."}, handle)
            send({"type": "done", "audio_seconds": 0.5, "sample_rate": SAMPLE_RATE})
        elif MODE == "fail_run":
            send({"type": "error", "code": "speech_failed", "message": "Qwen3-TTS failed: test"})
        elif MODE == "crash_run":
            sys.exit(3)
        else:
            send({"type": "progress", "value": 0.5, "message": "Sentence 1 of 2"})
            send({"type": "progress", "value": 1.0, "message": "Sentence 2 of 2"})
            write_silence(message["output"])
            send({"type": "done", "audio_seconds": 0.5, "sample_rate": SAMPLE_RATE})


if __name__ == "__main__":
    main()
