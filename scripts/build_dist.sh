#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -U pip
python -m pip install build twine

rm -rf dist build
python -m build
python -m twine check dist/*

echo "OK"
