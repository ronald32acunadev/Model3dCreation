import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from model3d_contracts.events import ResultStats
from model3d_contracts.jobs import JobOptions
from model3d_contracts.library import Entry, Library, Outcome
from model3d_engine.api.app import create_app
from model3d_engine.api.tasks import ToolTasks
from model3d_engine.config import Settings
from model3d_engine.jobs.events import EventBus
from model3d_engine.jobs.scheduler import Scheduler
from model3d_engine.jobs.store import JobStore

from .fakes import FakeRunner
from .test_api import MODE, image_bytes

CONVERT_STUB = r'''
import sys
from pathlib import Path

def main():
    script = Path(__file__).resolve()
    mode = script.with_name("mode").read_text().strip()
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    if mode == "fail":
        print(f"Could not write {target}", file=sys.stderr)
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lstrip(".")
    target.write_bytes(b"converted-" + suffix.encode())
    if mode == "multi":
        target.with_suffix(".mtl").write_bytes(b"mtl")
        (target.parent / "texture.png").write_bytes(b"png")
    sys.exit(0)

if __name__ == "__main__":
    main()
'''

ANIMATE_STUB = r'''
import sys
from pathlib import Path

def main():
    script = Path(__file__).resolve()
    mode = script.with_name("mode").read_text().strip()
    if mode == "fail":
        print("No legs found to animate", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "detect":
        print("bpy noise")
        print('MODEL3D_RESULT {"body":"biped","legs":2,"arms":2,"up":"Z"}')
        sys.exit(0)
    elif cmd == "walk":
        source = Path(sys.argv[2])
        target = Path(sys.argv[3])
        body = sys.argv[4]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"walk-glb")
        print('MODEL3D_RESULT {"body":"biped","frames":24}')
        sys.exit(0)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
'''


def write_stubs(folder: Path, mode: str = "ok") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "convert_model.py").write_text(CONVERT_STUB)
    (folder / "animate_rig.py").write_text(ANIMATE_STUB)
    (folder / "mode").write_text(mode)
    return folder


def set_mode(folder: Path, mode: str) -> None:
    (folder / "mode").write_text(mode)


def make_tool_client(tmp_path) -> tuple[TestClient, Library, Path]:
    settings = Settings(home=tmp_path, hunyuan_dir=tmp_path / "hy")
    store, bus = JobStore(settings.jobs_dir), EventBus()
    scheduler = Scheduler(store, bus, FakeRunner("shape.glb"), FakeRunner("model.glb"))
    stubs = write_stubs(tmp_path / "stubs")
    app = create_app(
        settings, scheduler, store, bus,
        health=lambda: {"ready": scheduler.ready, "mode": MODE, "gpus": [], "rig": False, "rig_reason": None},
        tool_tasks=ToolTasks(sys.executable, stubs),
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    lib = Library(tmp_path / "library", recover=False)
    return client, lib, stubs


def add_model_entry(lib, tmp_path, name, rig=False) -> Entry:
    img = tmp_path / f"{name}.png"
    img.write_bytes(image_bytes())
    entry = lib.create(img, JobOptions(rig=rig), name=name)
    lib.model_path(entry.entry_id).write_bytes(b"glb-" + name.encode())
    entry = entry.model_copy(update={
        "outcome": Outcome.succeeded,
        "seed": 7,
        "stats": ResultStats(vertices=10, faces=20, textured=True, duration_s=1.5),
    })
    lib.update(entry)
    return entry


def wait_for(client, url, done, timeout=15.0):
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(url)
        if done(response):
            return response
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {url}")
        time.sleep(0.05)
