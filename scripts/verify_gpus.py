"""Validate the engine environment on the dual RTX 5070 workstation.

Run from the repository root with the engine environment:

    uv run --project engine python scripts/verify_gpus.py

Exits 0 when no check fails (warnings allowed), 1 otherwise.
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from model3d_engine.gpu.topology import (
    NVIDIA_SMI_COMMAND,
    GpuLink,
    bus_id_from_props,
    normalize_bus_id,
    parse_nvidia_smi,
)

# CUDA indexes must follow PCI bus order so they match nvidia-smi. This has to be set
# before the first `import torch`, which is why torch is only imported inside functions.
os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")

GIB = 1024**3


def plan_placement(links: list[GpuLink]) -> dict[str, GpuLink]:
    """Assign each pipeline stage to a GPU.

    Paint needs about 21 GB of VRAM and must stream offloaded weights from RAM, so it
    goes on the widest link. Shape fits in 12 GB and stays resident, so the narrow link
    only costs time when its weights first load.
    """
    if len(links) != 2:
        raise ValueError(f"expected 2 GPUs, found {len(links)}")
    paint, shape = sorted(links, key=lambda link: (-link.bandwidth_gbps, link.bus_id))
    return {"shape": shape, "paint": paint}


def parse_nvcc_version(text: str) -> tuple[int, int]:
    match = re.search(r"release (\d+)\.(\d+)", text)
    if not match:
        raise ValueError("Could not find the CUDA release in the nvcc output")
    return int(match.group(1)), int(match.group(2))


def cuda_majors_match(torch_cuda: str | None, nvcc: tuple[int, int]) -> bool:
    if torch_cuda is None:
        return False
    return int(torch_cuda.split(".")[0]) == nvcc[0]


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # 'pass', 'warn', or 'fail'
    detail: str


def _find_nvcc() -> Path | None:
    cuda_home = os.environ.get("CUDA_HOME")
    if cuda_home and (Path(cuda_home) / "bin" / "nvcc").exists():
        return Path(cuda_home) / "bin" / "nvcc"
    found = shutil.which("nvcc")
    if found:
        return Path(found)
    default = Path("/usr/local/cuda/bin/nvcc")
    return default if default.exists() else None


def _check_nvcc(torch_cuda: str | None, compare: bool) -> Check:
    try:
        path = _find_nvcc()
        if path is None:
            return Check("nvcc", "fail", "missing; install cuda-toolkit or set CUDA_HOME")
        result = subprocess.run(
            [str(path), "--version"], check=True, capture_output=True, text=True, timeout=30
        )
        major, minor = parse_nvcc_version(result.stdout)
        if compare and not cuda_majors_match(torch_cuda, (major, minor)):
            return Check("nvcc", "fail", f"torch CUDA {torch_cuda} does not match nvcc {major}.{minor}")
        return Check("nvcc", "pass", f"{major}.{minor} at {path}")
    except Exception as error:
        return Check("nvcc", "fail", str(error))


def _check_nvidia_smi() -> tuple[Check, list[GpuLink] | None, dict[str, GpuLink] | None]:
    try:
        result = subprocess.run(
            NVIDIA_SMI_COMMAND, check=True, capture_output=True, text=True, timeout=30
        )
        links = parse_nvidia_smi(result.stdout)
    except Exception as error:
        return Check("nvidia-smi", "fail", str(error)), None, None
    try:
        placement = plan_placement(links)
    except ValueError as error:
        # Keep the parsed links so the device order check can still compare them.
        return Check("nvidia-smi", "fail", str(error)), links, None
    detail = ", ".join(f"{link.bus_id} PCIe gen{link.pcie_gen} x{link.pcie_width}" for link in links)
    return Check("nvidia-smi", "pass", detail), links, placement


def run_checks(repo_root: Path) -> tuple[list[Check], dict[str, GpuLink] | None]:
    checks: list[Check] = []

    try:
        import torch

        checks.append(Check("torch", "pass", f"{torch.__version__} (CUDA {torch.version.cuda})"))
    except Exception as error:
        checks.append(Check("torch", "fail", str(error)))
        smi_check, _, placement = _check_nvidia_smi()
        checks.append(smi_check)
        checks.append(_check_nvcc(None, compare=False))
        return checks, placement

    def add(name: str, test) -> None:
        try:
            status, detail = test()
            checks.append(Check(name, status, detail))
        except Exception as error:
            checks.append(Check(name, "fail", str(error)))

    add("cuda available", lambda: ("pass" if torch.cuda.is_available() else "fail", ""))
    add("sm_120 kernels", lambda: ("pass" if "sm_120" in torch.cuda.get_arch_list() else "fail", ""))
    try:
        count = torch.cuda.device_count()
    except Exception:
        count = 0
    add("device count", lambda: ("pass" if count == 2 else "fail", f"found {count}"))

    torch_bus_ids = []
    for index in range(count):
        try:
            props = torch.cuda.get_device_properties(index)
        except Exception as error:
            checks.append(Check(f"cuda:{index}", "fail", str(error)))
            continue
        bus_id = bus_id_from_props(props.pci_domain_id, props.pci_bus_id, props.pci_device_id)
        torch_bus_ids.append(bus_id)

        def device_test(index=index, props=props):
            detail = f"{props.name}, sm_{props.major}{props.minor}, {props.total_memory / GIB:.1f} GiB"
            if (props.major, props.minor) != (12, 0) or props.total_memory < 11 * GIB:
                return "fail", detail
            a = torch.randn(1024, 1024, device=f"cuda:{index}", dtype=torch.float16)
            b = a @ a
            torch.cuda.synchronize(index)
            if not bool(torch.isfinite(b).all()):
                return "fail", f"{detail} (matmul smoke test returned non-finite values)"
            return "pass", detail

        add(f"cuda:{index} {bus_id}", device_test)

    for index in range(count):

        def vram_test(index=index):
            free, _total = torch.cuda.mem_get_info(index)
            if free < 10 * GIB:
                return "warn", f"{free / GIB:.1f} GiB free; unload LM Studio/Ollama models before generating"
            return "pass", f"{free / GIB:.1f} GiB free"

        add(f"free VRAM cuda:{index}", vram_test)

    smi_check, links, placement = _check_nvidia_smi()
    checks.append(smi_check)

    def order_test():
        if links is None:
            return "fail", "nvidia-smi unavailable"
        expected = sorted(link.bus_id for link in links)
        if torch_bus_ids != expected:
            return "fail", f"torch order {torch_bus_ids} != {expected}; set CUDA_DEVICE_ORDER=PCI_BUS_ID"
        return "pass", ""

    add("device order", order_test)
    checks.append(_check_nvcc(torch.version.cuda, compare=True))

    def rasterizer_test():
        import custom_rasterizer_kernel  # noqa: F401

        return "pass", ""

    add("custom_rasterizer", rasterizer_test)

    def inpaint_test():
        sys.path.insert(0, str(repo_root / "vendor" / "Hunyuan3D-2.1" / "hy3dpaint"))
        from DifferentiableRenderer import mesh_inpaint_processor  # noqa: F401

        return "pass", ""

    add("mesh_inpaint_processor", inpaint_test)
    return checks, placement


def format_report(checks: list[Check], placement: dict[str, GpuLink] | None) -> str:
    lines = [f"[{check.status.upper()}] {check.name}: {check.detail}" for check in checks]
    lines.append("")
    if placement is not None:
        for stage in ("shape", "paint"):
            link = placement[stage]
            lines.append(
                f"{stage} -> {link.bus_id} (PCIe gen{link.pcie_gen} x{link.pcie_width}, "
                f"{link.bandwidth_gbps:.1f} GB/s)"
            )
    failed = sum(1 for check in checks if check.status == "fail")
    lines.append("RESULT: OK" if failed == 0 else f"RESULT: FAILED ({failed} checks)")
    return "\n".join(lines)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    checks, placement = run_checks(repo_root)
    print(format_report(checks, placement))
    return 0 if all(check.status != "fail" for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
