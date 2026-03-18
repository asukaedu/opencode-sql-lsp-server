#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ "${BUILD_DIST_USE_VENV:-0}" == "1" ]]; then
  "$PYTHON_BIN" -m venv .venv
  source .venv/bin/activate
fi

"$PYTHON_BIN" -m pip install -U pip
"$PYTHON_BIN" -m pip install build twine

rm -rf dist build
"$PYTHON_BIN" -m build
"$PYTHON_BIN" -m twine check dist/*

printf 'Built artifacts:\n'
"$PYTHON_BIN" - <<'PY'
from pathlib import Path

for path in sorted(Path("dist").glob("*")):
    print(path.name)
PY
