"""Generate each part crop alone through the running engine, with the mold job's seed.

`python generate_parts.py RUN_DIR [--engine URL]` submits crops/<part>.png as `high` jobs without
rig, waits for each, and copies its result to parts/<part>.glb. Finished parts are skipped, so a
rerun resumes. parts.json records each part's job id, state, and seconds.
"""

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

POLL_S = 5.0


def submit(engine: str, image: Path, options: dict) -> str:
    """POST /v1/jobs as multipart form data and return the job id."""
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"options\"\r\n\r\n{json.dumps(options)}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{image.name}\"\r\nContent-Type: image/png\r\n\r\n".encode(),
        image.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"{engine}/v1/jobs", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())["job_id"]


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--engine", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)
    run = Path(args.run)
    seed = json.loads((run / "job.json").read_text())["seed"]
    crops = json.loads((run / "crops.json").read_text())["parts"]
    (run / "parts").mkdir(exist_ok=True)
    record_path = run / "parts.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}

    for part in crops:
        target = run / "parts" / f"{part}.glb"
        if target.exists():
            continue
        started = time.monotonic()
        job_id = submit(args.engine, run / "crops" / f"{part}.png", {"quality": "high", "seed": seed, "rig": False})
        record[part] = {"job_id": job_id, "state": "queued"}
        record_path.write_text(json.dumps(record, indent=2))
        while True:
            job = get_json(f"{args.engine}/v1/jobs/{job_id}")
            if job["state"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(POLL_S)
        record[part].update(state=job["state"], seconds=round(time.monotonic() - started), error=job.get("error"))
        record_path.write_text(json.dumps(record, indent=2))
        if job["state"] == "succeeded":
            with urllib.request.urlopen(f"{args.engine}/v1/jobs/{job_id}/result", timeout=300) as response:
                target.write_bytes(response.read())
        print(json.dumps({part: record[part]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
