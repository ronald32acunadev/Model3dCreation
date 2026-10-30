"""Stdlib-only stand-in for trellis_worker.py, steered by FAKE_TRELLIS_MODE (tests only)."""

import json
import os
import sys

MODE = os.environ.get("FAKE_TRELLIS_MODE", "ok")


def send(message: dict) -> None:
    print(json.dumps(message), flush=True)


def main() -> None:
    print("[SPARSE] Conv backend: flex_gemm", flush=True)
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
            cuda_device_order=os.environ.get("CUDA_DEVICE_ORDER"),
            post_device=os.environ.get("MODEL3D_TRELLIS_POST_DEVICE"),
        )
        with open(message["output"] + ".request.json", "w") as handle:
            json.dump(request, handle)
        if MODE == "fail_run":
            send({"type": "error", "code": "gpu_out_of_memory", "message": "TRELLIS.2 ran out of VRAM: test"})
        elif MODE == "crash_run":
            sys.exit(3)
        else:
            send({"type": "progress", "value": 0.3, "message": "Generating the shape"})
            with open(message["output"], "wb") as handle:
                handle.write(b"glTF-fake")
            send({"type": "done", "vertices": 8, "faces": 12})


if __name__ == "__main__":
    main()
