"""Inventory of the GPUs this machine can run the stages on.

PyTorch decides which GPUs are usable (CUDA_VISIBLE_DEVICES, driver, compiled
architectures), so the probe asks it in a spawned process: the API process never imports
torch. nvidia-smi only adds the PCIe link of each card.
"""

import importlib
import multiprocessing
import os
import subprocess
import time
from dataclasses import dataclass

from model3d_engine.gpu.topology import NVIDIA_SMI_COMMAND, GpuLink, bus_id_from_props, parse_nvidia_smi

PROBE_TARGET = "model3d_engine.gpu.probe:probe_main"


@dataclass(frozen=True)
class GpuInfo:
    """One GPU as PyTorch sees it: `index` is its CUDA index in PCI bus order."""

    index: int
    bus_id: str
    name: str
    memory_total_mib: int
    memory_free_mib: int
    capability: tuple[int, int]
    supported: bool
    bandwidth_gbps: float


@dataclass(frozen=True)
class Inventory:
    """The visible GPUs, and why none can be used when that is the case."""

    gpus: tuple[GpuInfo, ...]
    problem: str | None


def arch_supported(major: int, minor: int, arch_list: list[str]) -> bool:
    """Whether a PyTorch build with these architectures runs on compute capability major.minor.

    A GPU runs a build compiled for its own sm_XY, or one that ships compute_AB PTX for an
    older capability, which the driver compiles for the GPU.
    """
    capability = major * 10 + minor
    if f"sm_{capability}" in arch_list:
        return True
    return any(arch.startswith("compute_") and int(arch.removeprefix("compute_")) <= capability for arch in arch_list)


def _bandwidth(link: GpuLink | None) -> float:
    if link is None:
        return 0.0
    try:
        return link.bandwidth_gbps
    except ValueError:
        return 0.0


def merge_inventory(torch_gpus: list[dict], links: list[GpuLink] | None) -> Inventory:
    """Combine the probe's GPUs with nvidia-smi links; cards torch cannot see are left out."""
    by_bus = {link.bus_id: link for link in links or []}
    gpus = tuple(
        GpuInfo(
            index=raw["index"],
            bus_id=raw["bus_id"],
            name=raw["name"],
            memory_total_mib=raw["total_mib"],
            memory_free_mib=raw["free_mib"],
            capability=(raw["major"], raw["minor"]),
            supported=raw["supported"],
            bandwidth_gbps=_bandwidth(by_bus.get(raw["bus_id"])),
        )
        for raw in torch_gpus
    )
    if not gpus:
        return Inventory(gpus, "No NVIDIA GPU is visible to PyTorch")
    if not any(gpu.supported for gpu in gpus):
        problem = "; ".join(
            f"GPU {gpu.bus_id} (sm_{gpu.capability[0]}{gpu.capability[1]}) is not supported by this PyTorch build"
            for gpu in gpus
        )
        return Inventory(gpus, problem)
    return Inventory(gpus, None)


def probe_main(conn) -> None:
    """Child process: list the CUDA GPUs with their free memory and architecture support."""
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # before torch is imported
    try:
        import torch

        gpus = []
        if torch.cuda.is_available():
            arch_list = torch.cuda.get_arch_list()
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                free, total = torch.cuda.mem_get_info(index)
                gpus.append(
                    {
                        "index": index,
                        "bus_id": bus_id_from_props(props.pci_domain_id, props.pci_bus_id, props.pci_device_id),
                        "name": props.name,
                        "total_mib": total // 2**20,
                        "free_mib": free // 2**20,
                        "major": props.major,
                        "minor": props.minor,
                        "supported": arch_supported(props.major, props.minor, arch_list),
                    }
                )
        conn.send(("gpus", gpus))
    except Exception as error:
        conn.send(("error", f"{type(error).__name__}: {error}"))
    os._exit(0)  # skip CUDA teardown, which can hang


def _run_target(target: str, conn) -> None:
    module_name, attr_name = target.split(":", 1)
    getattr(importlib.import_module(module_name), attr_name)(conn)


def _read_links() -> list[GpuLink] | None:
    try:
        result = subprocess.run(NVIDIA_SMI_COMMAND, check=True, capture_output=True, text=True, timeout=30)
        return parse_nvidia_smi(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def probe_gpus(timeout_s: float = 60.0, target: str = PROBE_TARGET) -> Inventory:
    """Ask a spawned torch process for the GPUs; a crash or a hang becomes the problem."""
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_run_target, args=(target, child_conn), daemon=True, name="model3d-gpu-probe")
    process.start()
    child_conn.close()
    deadline = time.monotonic() + timeout_s
    message = None
    try:
        while message is None:
            if parent_conn.poll(0.1):
                try:
                    message = parent_conn.recv()
                except EOFError:
                    process.join(5)
                    return Inventory((), f"GPU probe failed: the probe exited with code {process.exitcode}")
            elif not process.is_alive():
                return Inventory((), f"GPU probe failed: the probe exited with code {process.exitcode}")
            elif time.monotonic() > deadline:
                return Inventory((), f"GPU probe did not answer within {timeout_s:.0f} s")
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        parent_conn.close()
    kind, payload = message
    if kind == "error":
        return Inventory((), f"GPU probe failed: {payload}")
    return merge_inventory(payload, _read_links())
