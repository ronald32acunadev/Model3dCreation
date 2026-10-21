# Engine foundation (phases 0 and 1)

Objective: turn `Fases/fase-0` and `Fases/fase-1` into a verified architecture spec and a working GPU environment on this workstation.
Branch: `main` (no commits yet; the owner decides delivery). Test runner: pytest via uv (from phase 1).

## Specs

- S1 (L2): "Comienza ejecutando la fase 0 y luego la 1"
- S2 (phase 0): fill the missing parts of `Fases/fase-0-arquitectura-y-stack.md` (architecture overview, hardware mapping, full repository structure, REST and WebSocket contracts), in English, with facts verified on this machine.
- S3 (phase 1): system dependencies, Python environment, PyTorch with CUDA for the RTX 5070, compiled C++/CUDA extensions, and `scripts/verify_gpus.py` validating both GPUs.
- Assumption: the spec's stated versions (Python 3.11, CUDA 12.4, PyTorch 2.5) are replaced by versions verified to support sm_120; recorded in the phase 0 document.

## Tasks

- [x] T1 (S2) inline: phase 0 architecture document `docs/architecture.md`.
- [x] T2 (S3) inline: phase 1 environment (uv, Python, PyTorch, CUDA toolkit, extensions). Evidence: L5.
- [x] T3 (S3) inline: `scripts/verify_gpus.py` with tests. Evidence: L6.
- [x] T4 (S3, follow-up) inline: end-to-end shape + paint spike on both GPUs. Evidence: L7.

## Log

- L1 (2026-10-20): "OK lee cada una de las fases y comenzemos con la fase 0"
- L2 (2026-10-20): "Comienza ejecutando la fase 0 y luego la 1"
- L3 (2026-10-20): owner cleaned the repository to the template on `main`; `.model3d/` (Hunyuan3D clone and 6.9 GB of weights) is gone. "Si me parece bien" to resyncing AGENTS.md/CLAUDE.md.
- L4: evidence for T1. nvidia-smi: GPU0 `25:00.0` link x2 (gen2 max), GPU1 `29:00.0` x16 (gen3 max), both compute capability 12.0, driver 595.99.02. Hunyuan3D 2.1 README: "10 GB VRAM for shape generation, 21GB for texture generation". Blender 5.2.2 embeds Python 3.13.13. Ubuntu `nvidia-cuda-toolkit` is 12.4 (no sm_120); multiverse has `cuda-toolkit-13-1`. PyTorch cu130 wheels exist up to 2.14.1 for cp310.
- L5: owner installed `cuda-toolkit-13-1` and approved downloads ("Listo"). Python switched to 3.11: paint imports `bpy`, and `bpy==4.0` for cp310 is gone from PyPI (4.2-5.0 are cp311). `engine/` uv project: torch 2.9.1+cu130, bpy 4.5.14; `custom_rasterizer` builds with gcc 15 + nvcc 13.1 (39 s); `mesh_inpaint_processor` rebuilt with the venv ABI suffix; full shape and paint pipelines import after `torchvision_fix.apply_fix()`. Weights: Hunyuan3D 2.1 14 GB, DINOv2-giant 4.3 GB in `~/.local/share/model3d/hf`.
- L6: code synthesized by pumbalama (qwen3-coder:30b; pumbastudio skipped because Ollama already held 20 GB of VRAM). First answer had 8 defects; the follow-up regressed because the stateless MCP call omitted the code, so the agent applied the 8 fixes to the first answer. Tests written together with the code (no separate RED run). `uv run pytest`: 12 passed; `-m gpu`: 1 passed; `verify_gpus.py`: RESULT OK, free-VRAM warnings from Ollama.
- L7 (2026-10-20): owner: "dale eliminalo" (Ollama model unloaded with `ollama stop`, not deleted), "En las pruebas trata de usar las 2 gpus", "Tengo 2 gpus si , no puedo hacer mas lo siento". Spike (throwaway, scratchpad) generated a textured GLB from upstream's demo image: shape on GPU0 77 s / 7.6 GiB; paint diffusion on GPU1 55 s / 8.7 GiB; DINOv2 + VAE decode on GPU0 2.9 GiB. Render: `docs/acceptance/2026-10-20-phase1-spike.png`. Fixes on the way: bpy before pymeshlab (embree), setuptools<81, trimesh==4.4.7 + open3d==0.18.0, onnxruntime>=1.20 (1.16.3 fails on glibc 2.43), numpy 1.26.4, upstream pins aligned, `engine/patches/hunyuan3d-paint-device.patch` applied to vendor and to the HF snapshot copy. Layouts A (CPU offload) and B were superseded by the measured hybrid; upstream `multiple_devices` dropped (dead code, not needed). `docs/architecture.md` and `docs/setup.md` updated.
- Next: phase 2 (engine backend). No commits; the owner decides delivery.
