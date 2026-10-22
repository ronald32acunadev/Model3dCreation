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
export HF_HOME=data/hf   # run from the repository root; the engine reads data/ by default
engine/.venv/bin/python -c "
from huggingface_hub import snapshot_download as get
get('tencent/Hunyuan3D-2.1', allow_patterns=['hunyuan3d-dit-v2-1/*', 'hunyuan3d-paintpbr-v2-1/*'])
get('facebook/dinov2-giant', allow_patterns=['*.json', 'model.safetensors'])"
```

The background-removal model (`rembg`, u2net) downloads on first use, into `data/u2net/`. All runtime data (weights, u2net, job folders, the desktop app's library) lives in the git-ignored `data/` folder at the repository root; `MODEL3D_HOME` points it elsewhere.

## 5. Patch the paint attention device

Upstream hardcodes `.to("cuda:0")` in the paint attention processor, so paint fails on any other GPU. The file exists twice: in the clone, and in the model snapshot (which is the copy diffusers loads). Patch both; the snapshot file is a symlink into the shared blob store, so replace it with a patched copy instead of editing the blob:

```bash
git -C vendor/Hunyuan3D-2.1 apply ../../engine/patches/hunyuan3d-paint-device.patch
F=$(ls -d data/hf/hub/models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-paintpbr-v2-1/unet/attn_processor.py)
cp --remove-destination "$(readlink -f "$F")" "$F"
patch "$F" engine/patches/hunyuan3d-paint-device.patch
```

## 5b. Rigging (optional): UniRig

Skeletons (`"rig": true`, `POST /v1/rig-jobs`) come from [UniRig](https://github.com/VAST-AI-Research/UniRig) (MIT code and weights), which needs its own environment. Without it the engine runs normally and reports `"rig": false` in health. From the repository root, with `CUDA_HOME` as in section 3:

```bash
git clone https://github.com/VAST-AI-Research/UniRig.git vendor/UniRig
git -C vendor/UniRig checkout 6793c66
cd vendor/UniRig
uv venv --python 3.11 .venv
uv pip install --python .venv torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu130
grep -v flash_attn requirements.txt > /tmp/unirig-requirements.txt
uv pip install --python .venv -r /tmp/unirig-requirements.txt spconv-cu126 numpy==1.26.4 ninja packaging psutil setuptools wheel
uv pip install --python .venv torch_scatter torch_cluster -f https://data.pyg.org/whl/torch-2.9.1+cu130.html
FLASH_ATTN_CUDA_ARCHS=120 MAX_JOBS=6 NVCC_THREADS=2 PATH=/usr/local/cuda-13.1/bin:$PATH \
    uv pip install --python .venv --no-build-isolation flash-attn
cd ../..
```

`flash-attn` compiles from source for sm_120 (about 18 minutes with 6 jobs; more jobs need more RAM). `spconv-cu126` is the newest build and its kernels run on sm_120. The weights (5.8 GB) go into `data/hf` like Hunyuan3D's:

```bash
HF_HOME=data/hf vendor/UniRig/.venv/bin/python -c "
from huggingface_hub import hf_hub_download as get
for name in ('skeleton/articulation-xl_quantization_256/model.ckpt', 'skin/articulation-xl/model.ckpt'):
    get('VAST-AI/UniRig', name)"
```

Do not edit the clone: the engine loads UniRig's skin checkpoint through its own wrapper (`engine/src/model3d_engine/stages/unirig_job.py`), which allowlists the one class `torch.load` rejects. `MODEL3D_UNIRIG_DIR` points the engine at another clone.

## 6. Verify

```bash
uv run --project engine python scripts/verify_gpus.py
```

Every line must be `[PASS]` or `[WARN]`, ending with `RESULT: OK`. The report also prints the stage placement (shape on the x2 card, paint on the x16 card). A `free VRAM` warning means another process (LM Studio, Ollama) holds GPU memory; free it before generating.

## 7. Run the engine

```bash
uv run --project engine python -m model3d_engine
```

It serves `http://127.0.0.1:8765/v1` (see [api.md](api.md)), picks a run mode for the detected GPUs (a few seconds before the port opens), and loads the workers in about 50 s. Close GPU-heavy apps first: the mode is chosen from the free VRAM at startup. `MODEL3D_HOME`, `MODEL3D_PORT`, `MODEL3D_HUNYUAN_DIR`, `MODEL3D_UNIRIG_DIR`, `MODEL3D_MODE` (force a run mode), `MODEL3D_SHAPE_BUS` and `MODEL3D_PAINT_BUS` (force the cards; set both or neither), `MODEL3D_ALLOWED_ORIGINS` (browser origins allowed to submit or cancel jobs, comma-separated, empty by default), and `MODEL3D_STAGE_TIMEOUT_S` (default 900) override the defaults; `MODEL3D_HOST` must stay a loopback address.

## 8. Desktop app

The app has its own environment (PySide6, no PyTorch):

```bash
cd client && uv sync
uv run --project client python -m model3d_client
```

It starts the engine by itself, or uses one already running on port 8765. To add it to the application menu, install the launcher with this folder's absolute path:

```bash
sed "s|@CLIENT_DIR@|$PWD/client|" client/model3d.desktop > ~/.local/share/applications/model3d.desktop
```

`client/launch.sh` finds `uv` in `~/.local/bin` when the desktop session does not have it on `PATH`. After changing any user-visible string, run `client/scripts/update_translations.sh`, translate the new entries in `client/src/model3d_client/i18n/model3d_es.ts`, and run the script again.


## 9. Blender add-on

The add-on (`blender/model3d`) generates models, adds skeletons, and opens library models from Blender 4.2 or newer (tested on 5.2.2). It only talks to an engine that is already running, so open the desktop app first; when the engine does not answer, the panel says "Open the Model3D desktop app". Build the extension and install it:

```bash
blender --command extension build --source-dir blender/model3d --output-dir /tmp
blender --command extension install-file -r user_default -e /tmp/model3d-0.1.0.zip
```

Then, in Blender, open Edit > Preferences > Add-ons > Model3D and set "Model3D folder" to this repository (the folder that holds `data/library`). The panel is in the 3D viewport's sidebar (N), tab "Model3D". Its tests run outside Blender, plus a headless Blender check:

```bash
cd blender && uv run pytest              # engine client, library, job flow (no Blender needed)
cd blender && uv run pytest -m blender   # registers the add-on in blender -b and validates the manifest
```
