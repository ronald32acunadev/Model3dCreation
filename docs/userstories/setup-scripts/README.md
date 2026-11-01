# Setup scripts

Status: approved 2026-10-31 ("Todas"). US-01, US-04, and US-05 are complete, except that the `sudo` prompt in US-01 / AC-06 has not run for real. US-02 and US-03 wait for the user's run on Windows and on a Mac.

Found by the fresh-clone run, and fixed: the pip-built CUDA toolkit also needs `nvidia-cusparse` and six related packages, since torch's headers include `cusparse.h`. This machine's older toolkit had them, so a rerun here could not show it.

Request (verbatim): "Puedes generar un script para ubuntu, windows , y mac para descargar todas las librerias necesarias para poder ejecutar el proyecto"

## Context

Running from the repo today means rebuilding everything by hand. On 2026-10-30 and 2026-10-31 that meant `uv sync` in `engine/` with the CUDA 13.2 `nvcc`, UniRig's environment with `flash-attn` built for sm_120, TRELLIS.2 with its native extensions, and about 40 GB of weights. The wizard (`client/src/model3d_client/setup/recipe.py`) installs into a separate folder and does not cover TRELLIS.2.

What each system can run today (checked in the repo on 2026-10-31):

| Piece | Ubuntu | Windows | macOS |
| --- | --- | --- | --- |
| Client (PySide6) | Yes | Yes | Yes, not tried |
| Engine environment | Yes | No: `engine/pyproject.toml` restricts uv to Linux x86_64; port pending in `docs/userstories/cross-platform-native/` | No: `torch` is pinned to the CUDA 13 index, and `custom_rasterizer` compiles with `nvcc` |
| UniRig | Yes | Not tried; `flash-attn`, `torch_scatter`, and `torch_cluster` are open questions in the port backlog | No: needs CUDA |
| TRELLIS.2 | Yes | Not tried; upstream tests Linux only | No: needs CUDA and at least 24 GB upstream |
| Weights (download only) | Yes | Yes | Pointless without an engine |

Macs have no NVIDIA GPU. So a macOS script can install only the client, which cannot generate anything without an engine, and the client only talks to `127.0.0.1`. A script cannot change that; only a port of the engine to Apple's Metal could, and that port is not in this backlog.

Facts the scripts must handle, found while installing by hand:

- `vendor/UniRig/.venv` on this disk is a Windows environment (`Lib/`, `Scripts/`), and the disk is shared with Windows. The Linux install went to `vendor/UniRig-linux`, selected with `MODEL3D_UNIRIG_DIR`.
- 195 tracked files show as modified here only because of CRLF line endings, and there is no `.gitattributes`. `client/launch.sh` fails on Linux with `env: 'bash\r'`.
- On Ubuntu 26.04 only the CUDA 13.2 `nvcc` builds the torch 2.9 extensions. The toolkit lives in `~/.local/share/model3d/cuda-13.2/home` on this machine.
- `facebook/dinov3-vitl16-pretrain-lvd1689m`, needed by TRELLIS.2, is gated: the user must request access and log in to Hugging Face.
- `~/.config/model3d/config.json` from a wizard install makes the repo's client use the installed folder.
- A download can be cut mid-file, and `/tmp` does not survive a reboot.

## Assumptions to confirm when approving

- The scripts install into the repo, in `.venv` folders, `vendor/`, and `data/`, all ignored by git. They do not touch the wizard's install folder or its pointer.
- A script never deletes a virtual environment built for another system. It stops or, for UniRig, uses `vendor/UniRig-linux`, and it says so.
- Revision requested by the user on 2026-10-31 (verbatim): "Puedes en el script pedir la contraseña de sudo y luego continuar con todo lo demas". On Ubuntu the script asks for the password once, at the start, through `sudo -v`, keeps the credential alive while it runs, and then continues unattended. The password goes only to `sudo`: the script never reads, stores, or logs it.
- With that, the Ubuntu script installs the missing apt packages (compilers, `git`, `curl`, the libraries Qt needs). That is the only step that uses `sudo`, and it runs first, so no keep-alive is needed.
- The CUDA 13.2 toolkit needs no `sudo`: it is built from NVIDIA's pip wheels (`nvidia-cuda-nvcc` and friends) in `~/.local/share/model3d/cuda-13.2`, the way this machine's copy was built. The scripts always use this copy, because on Ubuntu 26.04 a system `nvcc` 13.1, 13.3, or 13.4 cannot compile the torch 2.9 extensions.
- NVIDIA drivers are not installed by any script: installing one can break the display and needs a reboot. A missing driver stops the run with instructions. On Windows, CUDA and Visual Studio Build Tools are detected and reported, not installed.
- TRELLIS.2 and UniRig are installed by default; `--no-trellis` and `--no-rig` skip them. Without a Hugging Face login, the DINOv3 download is skipped with instructions, and the rest continues.
- The Windows and macOS scripts are verified by the user on those systems, because this session can only run Linux.
- The Windows script is not a port. It stops at the engine with the reason until `cross-platform-native` lands.

## US-01: Ubuntu, one command

As a user on Ubuntu, I want one command that installs everything the project needs, so that I can run it from the repo without doing each step by hand.

Dependencies: US-04.

- US-01 / AC-01: On Ubuntu x86_64 with an NVIDIA GPU and CUDA 13 `nvcc`, `scripts/setup/setup-ubuntu.sh` leaves working `client/.venv` and `engine/.venv`, the Hunyuan3D clone at the pinned commit with its patch, `mesh_inpaint_processor`, and RealESRGAN, plus UniRig and TRELLIS.2 with their environments, and all weights in `data/`.
- US-01 / AC-02: Pins come from one place. Commits, indexes, and checkpoint lists are imported from `recipe.py`, so the script and the wizard cannot disagree. TRELLIS.2's pins (torch 2.9.0, xformers 0.0.33.post1, transformers below 5) live with the script.
- US-01 / AC-03: Running it again skips finished steps and resumes a cut download or build. It ends by printing each step as done, skipped, or failed, plus the exact command to start the app, including `MODEL3D_UNIRIG_DIR` when needed and the setting that ignores a wizard pointer.
- US-01 / AC-04: With no NVIDIA GPU or driver, or under 100 GB free, it stops before downloading and names what is missing. A failing step stops the run with its log path and leaves earlier steps intact.
- US-01 / AC-06: When apt packages are missing, the script asks for the password once, through `sudo`, before any other work. If the password is refused, it stops with nothing installed. When no package is missing, it does not ask.
- US-01 / AC-07: A missing CUDA 13.2 toolkit is installed without `sudo`. A missing NVIDIA driver stops the run before any download, with instructions.
- US-01 / AC-05: A foreign virtual environment in `engine/` or `client/` stops the run with an explanation. In `vendor/UniRig` it switches to `vendor/UniRig-linux`. Neither is deleted.

Verification: a rerun on this machine, where everything is installed, reports all steps skipped; `--dry-run` lists the plan; the app starts with the printed command and answers `/v1/health`. A clean install from nothing cannot run here without deleting working environments, so a run in a fresh clone folder that reuses the uv cache and `data/` covers it, with weights not downloaded again.

## US-02: Windows, as far as the port allows

As a user on Windows, I want a script that installs what works today and says clearly what does not, so that I do not lose time on a failure I cannot understand.

Dependencies: US-01.

- US-02 / AC-01: `scripts/setup/setup-windows.ps1` installs `uv` when missing, then `client\.venv`, the Hunyuan3D source, and the weights in `data\`.
- US-02 / AC-02: Before the engine step it checks whether `engine/pyproject.toml` allows Windows. While it does not, the script stops there with this reason and a pointer to `docs/userstories/cross-platform-native/`, and it exits with a nonzero code.
- US-02 / AC-03: It checks for an NVIDIA driver, CUDA 13, and Visual Studio Build Tools, and prints what is missing with its download page.

Verification: manual, by the user on Windows, with results recorded here. On Linux the script's parse is checked only if `pwsh` is available.

## US-03: macOS, the client only

As a user on a Mac, I want the script to tell me what can run, so that I do not download 40 GB for nothing.

Dependencies: US-01.

- US-03 / AC-01: `scripts/setup/setup-macos.sh` installs `uv` when missing and `client/.venv`, and it downloads no weights.
- US-03 / AC-02: It prints that the engine needs an NVIDIA GPU and that generation is not available on a Mac, and it exits with code 0 only if the client imports.
- US-03 / AC-03: `client/pyproject.toml` needs no change, or the needed change is recorded here. PySide6 publishes macOS wheels; the client on a Mac has not been tried.

Verification: manual, by the user on a Mac, with results recorded here.

## US-04: Line endings that work on every system

As a user who shares this repo between Windows and Linux, I want scripts to keep the line endings their system needs, so that `launch.sh` and the new scripts run after any checkout.

- US-04 / AC-01: A `.gitattributes` sets `*.sh text eol=lf` and `*.ps1 text eol=crlf`, and `text=auto` for the rest.
- US-04 / AC-02: After renormalizing, `git status` no longer lists files whose only change is line endings, and `client/launch.sh` starts on Linux without the `bash\r` error. Commits and working content are otherwise unchanged, shown by `git diff --ignore-space-at-eol`.

Verification: the commands above on this machine. Renormalizing rewrites line endings in the working tree across the repo, so it runs only after the single real pending change (one `.gitignore` line) is handled.

## US-05: Documentation

As a new user, I want the README to say which script to run on my system and what it can do there, so that I know before starting.

Dependencies: US-01 to US-03.

- US-05 / AC-01: The README's installation section lists the three scripts, the table of what each system can run, the sizes, and the manual prerequisites, in English.

Verification: documentation review.

## Excluded

- Porting the engine to Windows (`docs/userstories/cross-platform-native/`).
- Running on Apple GPUs.
- Installing NVIDIA drivers or CUDA.
- Changing the wizard.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | Fresh clone in a new folder, sharing only the weights: every step installed from nothing, then one real job through its engine (TRELLIS.2 + rig) | Passed: 277 s, 38 740 faces, 4096 px textures, skeleton present |
| US-01 / AC-02 | `client/tests/test_setup_repo.py::test_pins_come_from_the_recipe` | Passed |
| US-01 / AC-03 | Rerun on this machine: `inpaint` built, the other steps skipped, `pytest` kept in `client/.venv`; `test_unirig_env_keeps_an_existing_venv` | Passed; the fresh clone also resumed after a reboot, skipping finished steps |
| US-01 / AC-04 | `test_no_driver_stops_before_anything`, `test_low_disk_stops`, `test_ok_preflight_is_empty` | Passed |
| US-01 / AC-06 | `test_ubuntu_script_uses_sudo_only_for_apt`; real runs here asked for no password, as no package was missing | Partial: the prompt itself has not run, since that needs a missing package |
| US-01 / AC-07 | `test_no_driver_stops_before_anything`; toolkit built from nothing in the fresh clone | Passed |
| US-01 / AC-05 | `test_foreign_engine_env_is_reported_and_kept`, `test_unirig_dir_switches_for_a_windows_env`; real run used `vendor/UniRig-linux` here | Passed |
| US-02 / AC-01..AC-03 | `test_windows_stops_at_engine_while_linux_only`, `test_windows_runs_engine_when_allowed`, and `pwsh` parse (0 errors) passed; run on Windows pending | Pending manual run by the user |
| US-03 / AC-01..AC-03 | `test_macos_has_no_steps` passed; run on a Mac pending | Pending manual run by the user |
| US-04 / AC-01..AC-02 | `git status`, `git diff --ignore-space-at-eol`, `client/launch.sh` | Passed: all 195 blobs were already LF; `git status` lists only the real `.gitignore` line; `launch.sh` starts the app |
| US-05 / AC-01 | README "Option 2: Setup Scripts" reviewed | Passed |
