#!/usr/bin/env bash
# Build dist/model3d_<version>_amd64.deb from this repository: the desktop app, the engine sources, uv, and the Blender add-on.
# Usage: packaging/deb/build.sh [--out DIR]. UV_BINARY=/path/to/uv skips downloading uv.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
UV_VERSION="0.13.5"

OUT="$REPO/dist"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)
            OUT="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$REPO/client/pyproject.toml" | head -n 1)"
if [[ -z "$VERSION" ]]; then
    echo "Error: Could not extract version from $REPO/client/pyproject.toml" >&2
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/opt/model3d"

copy_tree() {
    local src="$1"
    local dest="$2"
    mkdir -p "$dest"
    tar -C "$src" --exclude=__pycache__ --exclude='*.pyc' --exclude=.venv --exclude=tests --exclude=.pytest_cache -cf - . | tar -C "$dest" -xf -
}

copy_tree "$REPO/client/src/model3d_client" "$APP/lib/model3d_client"
copy_tree "$REPO/contracts/src/model3d_contracts" "$APP/lib/model3d_contracts"

copy_tree "$REPO/engine/src" "$APP/engine/src"
copy_tree "$REPO/engine/patches" "$APP/engine/patches"
install -m 644 "$REPO/engine/pyproject.toml" "$APP/engine/"
install -m 644 "$REPO/engine/uv.lock" "$APP/engine/"

copy_tree "$REPO/contracts/src" "$APP/contracts/src"
install -m 644 "$REPO/contracts/pyproject.toml" "$APP/contracts/"
if [[ -f "$REPO/contracts/uv.lock" ]]; then
    install -m 644 "$REPO/contracts/uv.lock" "$APP/contracts/"
fi

install -D -m 644 "$REPO/scripts/verify_gpus.py" "$APP/scripts/verify_gpus.py"

if [[ -n "${UV_BINARY:-}" ]]; then
    install -D -m 755 "$UV_BINARY" "$APP/bin/uv"
else
    CACHE="$OUT/.cache"
    mkdir -p "$CACHE"
    UV_TAR="$CACHE/uv-$UV_VERSION.tar.gz"
    if [[ ! -f "$UV_TAR" ]]; then
        curl -fL --retry 3 -o "$UV_TAR.part" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz"
        mv "$UV_TAR.part" "$UV_TAR"
    fi
    tar -xzf "$UV_TAR" -C "$STAGE" uv-x86_64-unknown-linux-gnu/uv
    install -D -m 755 "$STAGE/uv-x86_64-unknown-linux-gnu/uv" "$APP/bin/uv"
    rm -rf "$STAGE/uv-x86_64-unknown-linux-gnu"
fi

mkdir -p "$APP/blender"
(cd "$REPO/blender/model3d" && zip -q -r -X "$APP/blender/model3d.zip" . -x '__pycache__/*' '*/__pycache__/*' '*.pyc')

install -D -m 755 "$HERE/model3d" "$STAGE/usr/bin/model3d"
install -D -m 644 "$HERE/model3d.desktop" "$STAGE/usr/share/applications/model3d.desktop"
install -D -m 644 "$HERE/model3d.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/model3d.svg"

mkdir -p "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/" "$HERE/control.in" > "$STAGE/DEBIAN/control"
install -m 755 "$HERE/postrm" "$STAGE/DEBIAN/postrm"

find "$STAGE" -type d -exec chmod 755 {} +
find "$APP/lib" "$APP/engine" "$APP/contracts" "$APP/scripts" -type f -exec chmod 644 {} +

DEB="$OUT/model3d_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null
echo "$DEB"
