#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m pytest -m "not smoke" tests/test_config.py tests/test_sqlfluff_adapter.py tests/test_server_behaviors.py tests/test_workspace_config.py tests/test_lsp_helpers.py tests/test_diagnostics_scheduler.py tests/test_docs_consistency.py -q
"$PYTHON_BIN" -m basedpyright src/opencode_sql_lsp_server
