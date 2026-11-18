# Prioritize Headless GPU for Model Generation and Processing

Status: US-01 and US-02 complete on branch `feature/prioritize-headless-gpu`. Verified with 434 passing engine tests and live GPU verification.

## Context

Request (verbatim, 2026-11-18):
"Parece que tengo un problema con el performance en mi caso tengo 2 gpus, es mas que todo en velocidad , en una tengo conectado los monitores en donde tiene menos potencia de velocidad la otra que esta libre es la que esta mas velocidad de datos quiero que le de preoridad a la grafica en donde no esten conectado los monitores para generar los modelos y demas pasos"

Follow-up requirement (verbatim, 2026-11-18):
"Si el plan es cuando se llame a ejcutar el proceso busque primero la grafica que este mas libre y con mayor velocidad y utilizarla para ir cargando los modelos y quitando cuando no se necesiten"

### Verified Hardware Topology

Investigation of the workstation via `nvidia-smi` confirms:
- **GPU 0 (`0000:25:00.0`)**: NVIDIA GeForce RTX 5070 12 GB.
  - Active display: `display_active=Enabled`, `display_attached=Yes` (monitors connected, running GNOME Shell, Xwayland, LM Studio, etc.).
  - PCIe link: PCIe Gen 2 x2 (~1.0 GB/s bandwidth) — severely bottlenecked.
  - Available VRAM: ~9.4 - 9.7 GiB free.
- **GPU 1 (`0000:29:00.0`)**: NVIDIA GeForce RTX 5070 12 GB.
  - Headless: `display_active=Disabled`, `display_attached=No` (no displays attached, 0 desktop overhead).
  - PCIe link: PCIe Gen 3 x16 (~15.8 GB/s bandwidth) — ~16x higher bus throughput.
  - Available VRAM: ~11.3 - 11.7 GiB free.

### Problem Analysis

Currently:
1. `GpuLink` and `GpuInfo` in `engine/src/model3d_engine/gpu/` do not query or store `display_active` or monitor attachment status from `nvidia-smi`.
2. GPU sorting (`_by_free_memory`) sorts solely by `(memory_free_mib, bandwidth_gbps)`.
3. In TRELLIS.2 (`choose_generator_mode`), the primary shape generation stage (`gen`) was placed on the second card (`usable[1]`, which was `0000:25:00.0`), leaving model generation, rigging, and voice bound to the slow, monitor-burdened card while `post` took the fast card.
4. When falling back to single-card modes or dual-card setups, the system does not prioritize headless GPUs over display GPUs, causing UI lag and slow tensor streaming across the crippled PCIe Gen 2 x2 link.

Goals:
- Detect `display_active` status in `nvidia-smi` and propagate it through `GpuLink` and `GpuInfo`.
- Prioritize headless GPUs (`display_active == False`) with higher bandwidth for primary model generation (`shape` / `trellis gen`), rigging, and voice pipelines.
- Update `verify_gpus.py` and engine mode planning to reflect and verify this priority.

---

## US-01: Headless GPU Detection in Topology and Probe

As an engine architect, I want `nvidia-smi` topology parsing and the GPU inventory probe to capture display activity (`display_active`) so that the engine knows which GPUs are driving displays and which are dedicated compute devices.

- **US-01 / AC-01**: `NVIDIA_SMI_COMMAND` in `model3d_engine.gpu.topology` queries `display_active` alongside bus id, name, PCIe specs, and total memory.
- **US-01 / AC-02**: `GpuLink` includes a boolean `display_active` field (defaulting to `False`). `parse_nvidia_smi` parses `display_active` (handling "Enabled" / "Disabled" case-insensitively) while remaining backward-compatible with 5-field outputs.
- **US-01 / AC-03**: `GpuInfo` in `model3d_engine.gpu.probe` includes `display_active: bool = False`. `merge_inventory` maps `display_active` from `GpuLink` to each `GpuInfo`.
- **US-01 / AC-04**: Unit tests in `engine/tests/test_topology.py` and `engine/tests/test_probe.py` verify that `display_active` is parsed and populated accurately.

---

## US-02: Headless and High-Bandwidth Prioritization for Model Generation

As a user, I want the engine to prioritize the headless GPU with higher PCIe bandwidth for model generation and auxiliary stages (rigging, voice) so that generation runs at maximum speed without display contention.

- **US-02 / AC-01**: GPU ranking in `model3d_engine.gpu.modes` sorts usable cards by priority: headless first (`not gpu.display_active`), then PCIe bandwidth (`bandwidth_gbps`), then free memory (`memory_free_mib`).
- **US-02 / AC-02**: In `choose_generator_mode` (TRELLIS.2), the primary generative stage (`gen`) and single-GPU fallback run on the prioritized headless card.
- **US-02 / AC-03**: In `choose_mode` (Hunyuan3D), the primary model generation stage (`shape`) runs on the prioritized headless card, with auxiliary stages (`rig`, `voice`) bound to the high-throughput GPU.
- **US-02 / AC-04**: Models load on demand when jobs run on the prioritized GPU and are released/unloaded when finished or idle, preventing idle VRAM retention on the card.
- **US-02 / AC-05**: `scripts/verify_gpus.py` checks and reports `display_active` status, confirming placement of primary stages on the headless high-bandwidth card.
- **US-02 / AC-06**: All test suites in `engine/tests/` (including `test_modes.py`, `test_main.py`, and `test_verify_gpus.py`) pass with updated mock topologies and placement assertions.

---

## QA Evidence and Traceability

| Story / criterion or change-related regression risk | Test file and test name, or reproducible manual procedure | Observed result and evidence |
| --- | --- | --- |
| `US-01 / AC-01..AC-04` | `engine/tests/test_topology.py`, `engine/tests/test_probe.py` | Passed (11 passed: 6-field `display_active` parsed and propagated) |
| `US-02 / AC-01..AC-04` | `engine/tests/test_modes.py`, `engine/tests/test_main.py` | Passed (47 passed: headless card prioritized for generation and single-gpu fallback) |
| `US-02 / AC-05` | `uv run --project engine python scripts/verify_gpus.py` | Passed (`shape -> 0000:29:00.0 (PCIe gen3 x16, 15.8 GB/s)`, `paint -> 0000:25:00.0 (PCIe gen2 x2, 1.0 GB/s)`) |
| `US-02 / AC-06` | `uv run --project engine pytest engine/tests/` | Passed (434 passed, 0 failures across the entire engine test suite) |
