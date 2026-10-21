"""PCI topology of the GPUs and the stage placement derived from it."""

import re
from dataclasses import dataclass

NVIDIA_SMI_COMMAND = [
    "nvidia-smi",
    "--query-gpu=pci.bus_id,name,pcie.link.gen.max,pcie.link.width.current,memory.total",
    "--format=csv,noheader,nounits",
]
BUS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{4,8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
# Usable GB/s per PCIe lane, by generation.
PCIE_LANE_GBPS = {1: 0.25, 2: 0.5, 3: 0.985, 4: 1.969, 5: 3.938}


def normalize_bus_id(raw: str) -> str:
    """Return a PCI bus id as lowercase 'DDDD:BB:DD.F' (domain cut to 4 hex digits)."""
    raw = raw.strip()
    if not BUS_ID_PATTERN.match(raw):
        raise ValueError(f"Invalid PCI bus id: {raw!r}")
    domain, rest = raw.split(":", 1)
    return f"{int(domain, 16) & 0xFFFF:04x}:{rest.lower()}"


def bus_id_from_props(domain: int, bus: int, device: int) -> str:
    return f"{domain:04x}:{bus:02x}:{device:02x}.0"


@dataclass(frozen=True)
class GpuLink:
    bus_id: str
    name: str
    pcie_gen: int
    pcie_width: int
    memory_mib: int

    @property
    def bandwidth_gbps(self) -> float:
        if self.pcie_gen not in PCIE_LANE_GBPS:
            raise ValueError(f"Unknown PCIe generation {self.pcie_gen} for {self.bus_id}")
        return PCIE_LANE_GBPS[self.pcie_gen] * self.pcie_width


def parse_nvidia_smi(text: str) -> list[GpuLink]:
    links = []
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            raise ValueError(f"Expected 5 fields, got {len(parts)}: {line!r}")
        try:
            gen, width, memory = int(parts[2]), int(parts[3]), int(parts[4])
        except ValueError as error:
            raise ValueError(f"Non-integer numeric field: {line!r}") from error
        links.append(GpuLink(normalize_bus_id(parts[0]), parts[1], gen, width, memory))
    return links


@dataclass(frozen=True)
class Placement:
    """Torch device strings for each stage; helpers run DINOv2 and the VAE decode."""

    shape: str
    paint: str
    helpers: str


def resolve_placement(links: list[GpuLink], shape_bus: str, paint_bus: str) -> Placement:
    """Map PCI bus ids to CUDA devices.

    With CUDA_DEVICE_ORDER=PCI_BUS_ID, a GPU's CUDA index is its position in ascending
    bus order. The paint helpers share the shape GPU.
    """
    if shape_bus == paint_bus:
        raise ValueError("shape and paint must be different GPUs")
    order = sorted(link.bus_id for link in links)
    for bus in (shape_bus, paint_bus):
        if bus not in order:
            raise ValueError(f"GPU {bus} not found")
    shape = f"cuda:{order.index(shape_bus)}"
    return Placement(shape=shape, paint=f"cuda:{order.index(paint_bus)}", helpers=shape)
