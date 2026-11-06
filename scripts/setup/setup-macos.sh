#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

export PATH="$HOME/.local/bin:$PATH"

dry_run=false
for arg in "$@"; do
    if [[ "$arg" == "--dry-run" ]]; then
        dry_run=true
    fi
done

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This script runs on macOS only. Use setup-ubuntu.sh on Linux or setup-windows.ps1 on Windows." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

cd "$REPO"
if [[ "$dry_run" == true && -e client/.venv/bin/python ]]; then
    echo "Dry run: client environment exists, skipping the dependency sync."
else
    uv sync --project client --frozen --no-dev --inexact
fi
exec client/.venv/bin/python scripts/setup/setup_repo.py "$@"
