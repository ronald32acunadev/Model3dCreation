#!/usr/bin/env bash
# Start the Model3D desktop app from anywhere; the app starts the engine itself.
set -euo pipefail
CLIENT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# Desktop launchers do not load the shell profile, so uv may be missing from PATH.
UV="$(command -v uv || true)"
UV="${UV:-$HOME/.local/bin/uv}"
exec "$UV" run --project "$CLIENT" python -m model3d_client "$@"
