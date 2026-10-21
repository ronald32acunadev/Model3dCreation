"""Engine settings read from the environment."""

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from model3d_engine.gpu.topology import normalize_bus_id

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    home: Path
    hunyuan_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    shape_bus: str = "0000:25:00.0"
    paint_bus: str = "0000:29:00.0"
    max_image_bytes: int = 20 * 1024 * 1024
    # Browser origins allowed to POST or DELETE; requests without an Origin header are always allowed.
    allowed_origins: tuple[str, ...] = ()
    # Longest a stage may run one task before its worker is killed; measured runs take 60-80 s.
    stage_timeout_s: float = 900.0

    @property
    def jobs_dir(self) -> Path:
        return self.home / "jobs"

    @property
    def hf_home(self) -> Path:
        return self.home / "hf"


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
    return Settings(
        home=Path(home).expanduser() if home else Path.home() / ".local/share/model3d",
        hunyuan_dir=Path(hunyuan_dir).expanduser() if hunyuan_dir else repo_root / "vendor/Hunyuan3D-2.1",
        host=env.get("MODEL3D_HOST", "127.0.0.1"),
        port=int(env.get("MODEL3D_PORT", "8765")),
        shape_bus=normalize_bus_id(env.get("MODEL3D_SHAPE_BUS", "0000:25:00.0")),
        paint_bus=normalize_bus_id(env.get("MODEL3D_PAINT_BUS", "0000:29:00.0")),
        allowed_origins=tuple(
            normalize_origin(origin) for origin in env.get("MODEL3D_ALLOWED_ORIGINS", "").split(",") if origin.strip()
        ),
        stage_timeout_s=float(env.get("MODEL3D_STAGE_TIMEOUT_S", "900")),
    )
