#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER_DIR="$ROOT_DIR/extensions/vscode-opencode-sql-lsp-wrapper"

cd "$WRAPPER_DIR"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to verify the VS Code wrapper" >&2
  exit 1
fi

npm ci
npm run verify
