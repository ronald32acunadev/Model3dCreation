#!/usr/bin/env bash
# Run the client tests on the system Python with apt's PySide6 and pydantic, as the .deb does.
# Needs: sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtwebenginewidgets python3-pyside6.qtwebsockets python3-pydantic
# and, for pytest-qt only: sudo apt install python3-pyside6.qttest
set -euo pipefail
cd "$(dirname "$0")/.."

VENV="$(mktemp -d)"
trap 'rm -rf "$VENV"' EXIT

# uv builds the venv without python3-venv; the interpreter is still the system one.
uv venv --quiet --python /usr/bin/python3 --system-site-packages "$VENV"
# Resolve the test tools against apt's versions of the packages the app shares with them.
APT_PACKAGES="pydantic pydantic-core typing-extensions annotated-types typing-inspection"
/usr/bin/python3 -c '
import sys
from importlib.metadata import version
print("\n".join(f"{name}=={version(name)}" for name in sys.argv[1:]))
' $APT_PACKAGES > "$VENV/constraints.txt"
uv pip install --quiet --python "$VENV" --constraint "$VENV/constraints.txt" \
    pytest pytest-qt fastapi uvicorn python-multipart websockets
# Drop the venv copies (same versions) so the apt ones are what the tests import.
# shellcheck disable=SC2086  # one argument per package name
uv pip uninstall --quiet --python "$VENV" $APT_PACKAGES

"$VENV/bin/python" -c "
import sys
import PySide6, pydantic
for m in (PySide6, pydantic):
    if not m.__file__.startswith('/usr/lib/python3/'):
        print(f'Error: {m.__name__} loaded from venv instead of system path', file=sys.stderr)
        sys.exit(1)
print(f'PySide6: {PySide6.__version__}, pydantic: {pydantic.__version__}')
" || { echo "System package verification failed" >&2; exit 1; }

PYTHONPATH="$PWD/src:$(cd ../contracts && pwd)/src" QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "$VENV/bin/python" -m pytest -q "$@"
