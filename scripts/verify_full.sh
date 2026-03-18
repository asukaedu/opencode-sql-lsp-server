#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

bash scripts/verify_fast.sh
"$PYTHON_BIN" -m pytest -m smoke -q
"$PYTHON_BIN" tests/smoke_lsp_stdio.py
bash scripts/build_dist.sh
