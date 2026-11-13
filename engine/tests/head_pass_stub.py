import sys
import os
import json
import signal
import time


def main():
    job = sys.argv[1]
    print("[run]  step one", flush=True)
    print("[run]  step two", flush=True)
    print("some tool output", flush=True)

    mode = os.environ.get("STUB_MODE", "")

    if mode == "applied":
        payload = {"model": os.environ["STUB_MODEL"], "applied": True, "reason": None}
        print("MODEL3D_HEAD_PASS " + json.dumps(payload), flush=True)
        sys.exit(0)

    if mode == "skipped":
        payload = {"model": os.environ["STUB_MODEL"], "applied": False, "reason": "no usable head"}
        print("MODEL3D_HEAD_PASS " + json.dumps(payload), flush=True)
        sys.exit(0)

    if mode == "notag":
        sys.exit(0)

    if mode == "fail":
        print("boom: bad thing", flush=True)
        sys.exit(1)

    if mode == "sleep":
        def handler(signum, frame):
            sys.exit(143)

        signal.signal(signal.SIGTERM, handler)
        print("[run]  waiting", flush=True)
        time.sleep(60)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
