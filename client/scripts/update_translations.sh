#!/usr/bin/env bash
# Extract the app's strings into the Spanish .ts, then compile it to the .qm the app loads.
# Run from anywhere; translate new entries in model3d_es.ts (Qt Linguist or a text editor),
# then run this script again so the committed .qm matches.
set -euo pipefail
cd "$(dirname "$0")/.."
I18N=src/model3d_client/i18n
mapfile -t SOURCES < <(find src/model3d_client -name '*.py' | sort)
uv run pyside6-lupdate "${SOURCES[@]}" -ts "$I18N/model3d_es.ts" -no-obsolete -source-language en -target-language es
uv run pyside6-lrelease "$I18N/model3d_es.ts" -qm "$I18N/model3d_es.qm"
