"""Stage factories loaded inside worker processes by the process runner tests."""

import os

import trimesh

from model3d_engine.stages.protocol import StageError, StageResult, StageTask


class EchoStage:
    """Reports the MODEL3D_TEST_FLAG variable as progress, then behaves per mode."""

    def __init__(self, mode: str = "echo", **_ignored):
        self.mode = mode

    def run(self, task: StageTask, progress) -> StageResult:
        progress(0.5, os.environ.get("MODEL3D_TEST_FLAG", ""))
        if self.mode == "crash":
            os._exit(3)
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
