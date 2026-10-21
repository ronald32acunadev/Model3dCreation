# Engine Environment Setup (Phase 1)

Verified on Ubuntu 26.04.1 with two RTX 5070 GPUs (driver 595.99.02). Versions and the reasons behind them are in [architecture.md](architecture.md#3-stack-corrected-versions).

## 1. System packages

```bash
sudo apt install cuda-toolkit-13-1
```

This provides `nvcc` 13.1 in `/usr/local/cuda`. Ubuntu's `nvidia-cuda-toolkit` package is CUDA 12.4, which cannot build for sm_120; do not use it. GCC 15 from the base system works with nvcc 13.1.

Install [uv](https://docs.astral.sh/uv/) if missing. It downloads Python 3.11 on its own.

## 2. Hunyuan3D 2.1 source

```bash
git clone --depth 1 https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git vendor/Hunyuan3D-2.1
curl -L -o vendor/Hunyuan3D-2.1/hy3dpaint/ckpt/RealESRGAN_x4plus.pth --create-dirs \
    https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
```

`vendor/` is git-ignored. The engine environment builds `custom_rasterizer` from this clone, so clone it before syncing.

## 3. Python environment

```bash
cd engine
export CUDA_HOME=/usr/local/cuda-13.1 TORCH_CUDA_ARCH_LIST=12.0 MAX_JOBS=8
uv sync --no-install-package basicsr --no-install-package realesrgan --no-install-package custom-rasterizer
uv sync
```

The first sync installs PyTorch so the second one can build `basicsr` and `custom_rasterizer` without build isolation (both import torch at build time). `custom_rasterizer` takes about 40 seconds to compile.

`mesh_inpaint_processor` is a plain pybind11 module, compiled in place with the engine's Python (upstream's script picks the system `python3-config`, which gives the wrong ABI suffix):

```bash
cd vendor/Hunyuan3D-2.1/hy3dpaint/DifferentiableRenderer
PY=../../../../engine/.venv/bin/python
c++ -O3 -Wall -shared -std=c++11 -fPIC $($PY -m pybind11 --includes) mesh_inpaint_processor.cpp \
    -o mesh_inpaint_processor$($PY -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
```

## 4. Model weights (about 19 GB)

```bash
export HF_HOME=~/.local/share/model3d/hf
engine/.venv/bin/python -c "
from huggingface_hub import snapshot_download as get
get('tencent/Hunyuan3D-2.1', allow_patterns=['hunyuan3d-dit-v2-1/*', 'hunyuan3d-paintpbr-v2-1/*'])
get('facebook/dinov2-giant', allow_patterns=['*.json', 'model.safetensors'])"
```

The background-removal model (`rembg`, u2net) downloads on first use.

## 5. Patch the paint attention device

Upstream hardcodes `.to("cuda:0")` in the paint attention processor, so paint fails on any other GPU. The file exists twice: in the clone, and in the model snapshot (which is the copy diffusers loads). Patch both; the snapshot file is a symlink into the shared blob store, so replace it with a patched copy instead of editing the blob:

```bash
git -C vendor/Hunyuan3D-2.1 apply ../../engine/patches/hunyuan3d-paint-device.patch
F=$(ls -d ~/.local/share/model3d/hf/hub/models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-paintpbr-v2-1/unet/attn_processor.py)
cp --remove-destination "$(readlink -f "$F")" "$F"
patch "$F" engine/patches/hunyuan3d-paint-device.patch
```

## 6. Verify

```bash
uv run --project engine python scripts/verify_gpus.py
```

Every line must be `[PASS]` or `[WARN]`, ending with `RESULT: OK`. The report also prints the stage placement (shape and the paint helpers on the x2 card, paint diffusion on the x16 card). A `free VRAM` warning means another process (LM Studio, Ollama) holds GPU memory; free it before generating.
