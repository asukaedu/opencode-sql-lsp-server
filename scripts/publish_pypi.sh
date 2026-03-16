#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d dist ]]; then
  echo "dist/ not found. Build first: python -m build" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi

echo "Publishing contents of dist/:"
ls -1 dist

export TWINE_USERNAME="__token__"
printf "Paste PyPI token (input hidden): " >&2
read -rs TWINE_PASSWORD
echo >&2

python3 -m twine upload dist/*

unset TWINE_PASSWORD
