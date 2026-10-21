"""Run the fake engine as a process, like the real one: python fake_engine_process.py PORT MODE.

MODE: ready, loading (never ready), error (health reports a start failure).
Exits cleanly on SIGTERM.
"""

import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fake_engine import FakeEngine, make_health  # noqa: E402


def main() -> None:
    port, mode = int(sys.argv[1]), sys.argv[2]
    engine = FakeEngine(port)
    if mode == "loading":
        engine.controller.health = make_health(ready=False)
    elif mode == "error":
        engine.controller.health = make_health(
            ready=False, error={"code": "worker_start_failed", "message": "paint worker ran out of memory"}
        )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    engine.start()
    print(f"fake engine on {port} ({mode})", flush=True)
    stop.wait()
    engine.stop()


if __name__ == "__main__":
    main()
