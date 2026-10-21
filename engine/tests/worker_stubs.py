"""Stage factories loaded inside worker processes by the process runner tests."""

import os
import time

import trimesh

from model3d_engine.stages.protocol import StageError, StageResult, StageTask


class EchoStage:
    """Reports the MODEL3D_TEST_FLAG variable as progress, then behaves per mode."""

    def __init__(self, mode: str = "echo", marker: str | None = None, **_ignored):
        self.mode = mode
        self.marker = marker
        if mode == "crash_then_fail_load" and marker and os.path.exists(marker + ".crashed"):
            # The first reload after the crash fails; the next one succeeds.
            if not os.path.exists(marker + ".load_failed"):
                open(marker + ".load_failed", "w").close()
                raise RuntimeError("model file busy")

    def run(self, task: StageTask, progress) -> StageResult:
        progress(0.5, os.environ.get("MODEL3D_TEST_FLAG", ""))
        if self.mode == "crash":
            os._exit(3)
        if self.mode in ("crash_once", "crash_then_fail_load") and not os.path.exists(self.marker + ".crashed"):
            open(self.marker + ".crashed", "w").close()
            os._exit(3)
        if self.mode == "hang_once" and not os.path.exists(self.marker + ".hung"):
            # Stuck like a native call that never returns; only a kill ends it.
            open(self.marker + ".hung", "w").close()
            time.sleep(3600)
        if self.mode == "oom":
            raise StageError("gpu_out_of_memory", "Paint ran out of VRAM")
        mesh = trimesh.creation.box()
        output = task.job_dir / "shape.glb"
        mesh.export(output)
        return StageResult(output=output, vertices=len(mesh.vertices), faces=len(mesh.faces))


class BrokenStage:
    """Fails while loading, like a missing model."""

    def __init__(self):
        raise RuntimeError("no model here")
