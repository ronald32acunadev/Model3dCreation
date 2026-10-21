"""Body of GET /v1/health: readiness, the run mode the engine chose, and its GPUs."""

from typing import Literal

from pydantic import BaseModel

from model3d_contracts.jobs import JobError


class HealthMode(BaseModel):
    """How the engine runs on this machine's GPUs."""

    name: Literal["dual_gpu", "single_gpu_resident", "single_gpu_swap", "shape_only", "unsupported"]
    texture: bool
    reason: str


class HealthGpu(BaseModel):
    """One detected GPU; `stage` is null when the engine does not use it."""

    bus_id: str
    stage: Literal["shape", "paint", "shape+paint"] | None
    ready: bool
    memory_used_mib: int | None
    memory_total_mib: int | None
    memory_free_mib: int | None  # free when the engine started, before its workers loaded


class Health(BaseModel):
    ready: bool
    mode: HealthMode
    gpus: list[HealthGpu]
    error: JobError | None
