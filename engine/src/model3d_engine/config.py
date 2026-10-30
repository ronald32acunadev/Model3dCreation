"""Engine settings read from the environment."""

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from model3d_engine.gpu.modes import ModeName
from model3d_engine.gpu.topology import normalize_bus_id

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    home: Path
    hunyuan_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    # Unset buses and mode mean automatic placement from the detected GPUs.
    shape_bus: str | None = None
    paint_bus: str | None = None
    mode: str | None = None
    max_image_bytes: int = 20 * 1024 * 1024
    # Models sent for rigging only; a dense untextured model is about 30 MB.
    max_model_bytes: int = 200 * 1024 * 1024
    # Browser origins allowed to POST or DELETE; requests without an Origin header are always allowed.
    allowed_origins: tuple[str, ...] = ()
    # Longest a stage may run one task before its worker is killed; measured runs take 60-80 s.
    stage_timeout_s: float = 900.0
    # UniRig's clone with its own .venv; rigging is unavailable when it is missing.
    unirig_dir: Path = REPO_ROOT / "vendor/UniRig"
    # Which pipeline generates the shape: Hunyuan3D (shape + paint) or TRELLIS.2 (shape + texture).
    generator: str = "hunyuan"
    # TRELLIS.2's clone with its own .venv; jobs fail with a clear error when it is missing.
    trellis_dir: Path = REPO_ROOT / "vendor/TRELLIS.2"

    @property
    def jobs_dir(self) -> Path:
        return self.home / "jobs"

    @property
    def hf_home(self) -> Path:
        return self.home / "hf"


GENERATORS = ("hunyuan", "trellis2")


def is_loopback_host(host: str) -> bool:
    """Whether a host name or bare IP address is loopback."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def load_settings(env: Mapping[str, str] | None = None, repo_root: Path | None = None) -> Settings:
    env = os.environ if env is None else env
    repo_root = REPO_ROOT if repo_root is None else repo_root
    home = env.get("MODEL3D_HOME")
    hunyuan_dir = env.get("MODEL3D_HUNYUAN_DIR")
    unirig_dir = env.get("MODEL3D_UNIRIG_DIR")
    shape_bus, paint_bus = env.get("MODEL3D_SHAPE_BUS"), env.get("MODEL3D_PAINT_BUS")
    if (shape_bus is None) != (paint_bus is None):
        raise ValueError("Set both MODEL3D_SHAPE_BUS and MODEL3D_PAINT_BUS, or neither")
    mode = env.get("MODEL3D_MODE")
    forcible = [name.value for name in ModeName if name != ModeName.unsupported]
    if mode is not None and mode not in forcible:
        raise ValueError(f"MODEL3D_MODE must be one of {', '.join(forcible)}; got {mode!r}")
    generator = env.get("MODEL3D_GENERATOR", "hunyuan")
    if generator not in GENERATORS:
        raise ValueError(f"MODEL3D_GENERATOR must be one of {', '.join(GENERATORS)}; got {generator!r}")
    trellis_dir = env.get("MODEL3D_TRELLIS_DIR")
    if generator == "trellis2" and any(env.get(name) is not None for name in ("MODEL3D_MODE", "MODEL3D_SHAPE_BUS", "MODEL3D_PAINT_BUS")):
        raise ValueError("MODEL3D_MODE, MODEL3D_SHAPE_BUS and MODEL3D_PAINT_BUS apply only to MODEL3D_GENERATOR=hunyuan")
    return Settings(
        home=Path(home).expanduser() if home else repo_root / "data",
        hunyuan_dir=Path(hunyuan_dir).expanduser() if hunyuan_dir else repo_root / "vendor/Hunyuan3D-2.1",
        unirig_dir=Path(unirig_dir).expanduser() if unirig_dir else repo_root / "vendor/UniRig",
        generator=generator,
        trellis_dir=Path(trellis_dir).expanduser() if trellis_dir else repo_root / "vendor/TRELLIS.2",
        host=env.get("MODEL3D_HOST", "127.0.0.1"),
        port=int(env.get("MODEL3D_PORT", "8765")),
        shape_bus=normalize_bus_id(shape_bus) if shape_bus is not None else None,
        paint_bus=normalize_bus_id(paint_bus) if paint_bus is not None else None,
        mode=mode,
        allowed_origins=tuple(
            normalize_origin(origin) for origin in env.get("MODEL3D_ALLOWED_ORIGINS", "").split(",") if origin.strip()
        ),
        stage_timeout_s=float(env.get("MODEL3D_STAGE_TIMEOUT_S", "900")),
    )
