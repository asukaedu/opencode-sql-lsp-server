#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
TWINE_USERNAME="${TWINE_USERNAME:-__token__}"
NON_INTERACTIVE="${NON_INTERACTIVE:-0}"

if [[ ! -d dist ]]; then
  echo "dist/ not found. Build first: python -m build" >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  exit 1
fi

echo "Publishing contents of dist/:"
ls -1 dist

if [[ -z "${TWINE_PASSWORD:-}" ]]; then
  if [[ "$NON_INTERACTIVE" == "1" || ! -t 0 ]]; then
    echo "TWINE_PASSWORD must be set for non-interactive publishing" >&2
    exit 1
  fi

  printf "Paste PyPI token (input hidden): " >&2
  read -rs TWINE_PASSWORD
  echo >&2
fi

export TWINE_USERNAME
export TWINE_PASSWORD

"$PYTHON_BIN" -m twine upload dist/*

unset TWINE_PASSWORD
