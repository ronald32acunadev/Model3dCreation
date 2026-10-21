"""Contract between the scheduler and the GPU stage runners."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from model3d_contracts.jobs import JobOptions

# File convention inside a job folder: the shape stage writes SHAPE_OUTPUT, the paint
# stage reads it and writes MODEL_OUTPUT, which is the job's result.
SHAPE_OUTPUT = "shape.glb"
MODEL_OUTPUT = "model.glb"


@dataclass(frozen=True)
class StageTask:
    """One job's input to a stage."""

    job_id: str
    job_dir: Path
    options: JobOptions
    seed: int


@dataclass(frozen=True)
class StageResult:
    """A stage's output mesh and its size."""

    output: Path
    vertices: int
    faces: int


class StageError(Exception):
    """A stage failure with a stable error code for the API."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


ProgressCallback = Callable[[float, str], None]


class StageRunner(Protocol):
    """Hosts one pipeline stage and runs tasks on it, one at a time."""

    ready: bool

    async def start(self) -> None: ...

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult: ...

    async def stop(self) -> None: ...
