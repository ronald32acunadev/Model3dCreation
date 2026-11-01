#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PACKAGES=(build-essential git curl patch libegl1 libxkbcommon0 libxcb-cursor0 libdbus-1-3)

export PATH="$HOME/.local/bin:$PATH"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "This script runs on Linux x86_64 only. Use setup-macos.sh or setup-windows.ps1 on other systems." >&2
    exit 1
fi

dry_run=false
for arg in "$@"; do
    if [[ "$arg" == "--dry-run" ]]; then
        dry_run=true
    fi
done

missing=()
for package in "${PACKAGES[@]}"; do
    status="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)"
    if [[ "$status" != "install ok installed" ]]; then
        missing+=("$package")
    fi
done

if (( ${#missing[@]} > 0 )); then
    echo "Missing apt packages: ${missing[*]}"
    echo "They provide the compilers and the libraries the desktop app needs."
    if [[ "$dry_run" == true ]]; then
        echo "Dry run: nothing installed."
    else
        if ! sudo -v; then
            echo "Nothing was installed." >&2
            exit 1
        fi
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    fi
fi

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

cd "$REPO"

if [[ -d client/.venv/Scripts ]]; then
    echo "client/.venv is a Windows environment. This script will not delete it: move or remove it by hand, then run this script again." >&2
    exit 2
fi

if [[ "$dry_run" == true && -e client/.venv/bin/python ]]; then
    echo "Dry run: client environment exists, skipping the dependency sync."
else
    uv sync --project client --frozen --no-dev --inexact
fi
exec client/.venv/bin/python scripts/setup/setup_repo.py "$@"
