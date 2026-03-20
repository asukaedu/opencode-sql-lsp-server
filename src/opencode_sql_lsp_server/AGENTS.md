# PACKAGE GUIDE

## OVERVIEW
Core SQL LSP implementation: CLI bootstrap, workspace/config resolution, debounced diagnostics, sqlfluff-backed formatting, and lightweight editor-assist features.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Start server process | `cli.py` | validates `--stdio`, optional workspace override |
| Add/change protocol handlers | `server.py` | all `@server.feature(...)` handlers live here |
| Change diagnostics scheduling | `diagnostics_scheduler.py` | debounce/cancel/task orchestration for lint publishing |
| Change LSP document helpers | `lsp_utils.py` | safe positions, issue ranges, token lookup |
| Change document/workspace symbols | `symbol_provider.py` | statement discovery for symbol handlers |
| Change dialect selection | `config.py` | default dialect + glob override matching |
| Change workspace-root/config caching | `workspace_config.py` | root selection + cached config fallback |
| Change lint/format integration | `sqlfluff_adapter.py` | keep third-party API usage isolated |
| Package version only | `__init__.py` | exports `__version__` |

## STRUCTURE
```text
opencode_sql_lsp_server/
├── cli.py
├── config.py
├── diagnostics_scheduler.py
├── lsp_utils.py
├── server.py
├── symbol_provider.py
├── sqlfluff_adapter.py
├── workspace_config.py
└── __init__.py
```

## CONVENTIONS
- Keep LSP feature registration in `server.py`; current handlers are initialize, didOpen, didChange, didSave, formatting, completion, hover, codeAction, documentSymbol, and workspaceSymbol.
- Keep the VS Code wrapper under `extensions/vscode-opencode-sql-lsp-wrapper/`; do not mix Node wrapper concerns into the Python package.
- Route document dialect lookup through `OpenCodeSqlLanguageServer.dialect_for_document()` so multi-root workspace behavior stays consistent.
- Preserve cache behavior in `workspace_config.py`: missing config returns defaults; invalid config reports once per mtime and can reuse cached config.
- Diagnostics are async/debounced. Reuse `DiagnosticsScheduler` and `run_lint_and_publish()` instead of spawning ad hoc background work.
- Large-document protections matter; respect `_MAX_LINT_LINES` and `_MAX_LINT_BYTES` when adding new lint-triggering flows.
- Preserve degraded-mode behavior in request handlers: formatting and code actions return `[]` on failure, but failures must still be surfaced through `report_server_error`.

## HANDOFF RECIPES
- Change an LSP handler in `server.py`
  - Touch points: `server.py`, maybe `diagnostics_scheduler.py`, `lsp_utils.py`, or `symbol_provider.py`
  - Do not touch: stdio-only CLI contract in `cli.py`
  - Targeted verification: `python3 -m pytest tests/test_server_behaviors.py tests/test_lsp_helpers.py tests/test_diagnostics_scheduler.py -q`
- Change diagnostics scheduling or debounce behavior
  - Touch points: `diagnostics_scheduler.py`, `server.py`
  - Do not touch: didOpen/didChange/didSave debounce semantics or large-document skip behavior
  - Targeted verification: `python3 -m pytest tests/test_server_behaviors.py tests/test_diagnostics_scheduler.py -q`
- Change dialect selection or workspace precedence
  - Touch points: `config.py`, `workspace_config.py`, `server.py`
  - Do not touch: default dialect fallback semantics or one-error-per-mtime behavior
  - Targeted verification: `python3 -m pytest tests/test_config.py tests/test_workspace_config.py -q`
- Change sqlfluff integration
  - Touch points: `sqlfluff_adapter.py`, maybe formatting/code-action handlers in `server.py`
  - Do not touch: degraded handler behavior (`[]` on formatting/code-action failure)
  - Targeted verification: `python3 -m pytest tests/test_sqlfluff_adapter.py tests/test_server_behaviors.py -q`
- Change build/release workflow
  - Touch points: `scripts/build_dist.sh`, `scripts/publish_pypi.sh`, `.github/workflows/ci.yml`
  - Do not touch: package entrypoint in `pyproject.toml`
  - Targeted verification: `bash scripts/build_dist.sh`
- Change the VS Code wrapper
  - Touch points: `extensions/vscode-opencode-sql-lsp-wrapper/`, `README.md`, `.github/workflows/ci.yml`
  - Do not touch: the Python server transport contract in `cli.py`
  - Targeted verification: `bash scripts/verify_vscode_wrapper.sh && python3 -m pytest tests/test_docs_consistency.py -q`

## ANTI-PATTERNS
- Do not import `sqlfluff` in `server.py`; keep vendor interaction in `sqlfluff_adapter.py`.
- Do not duplicate URI→filesystem root logic outside `workspace_config.py`.
- Do not publish diagnostics with raw sqlfluff coordinates; use `lsp_utils.issue_range()` / `lsp_utils.safe_position()` for document-safe bounds.
- Do not add synchronous heavy work to open/change/save handlers; existing design offloads linting through the event loop executor.
- Do not duplicate debounce/cancellation state outside `diagnostics_scheduler.py`.
- Do not treat formatting failures as fatal; current behavior returns `[]` on formatter failure.

## NOTES
- `OpenCodeSqlLanguageServer` uses `max_workers=2`; per-document pending timers/tasks now live behind `DiagnosticsScheduler`.
- Formatting replaces the full document with one `TextEdit`.
- Default dialect is `starrocks`; overrides use glob-style path matching with normalized `/` separators.
- StarRocks-aware completion/hover now includes common workflow phrases (materialized views, routine load, catalogs, partitioning/distribution, `UNNEST`, `JSON_EACH`).
- `symbol_provider.py` now extracts named StarRocks entities such as materialized views, routine load jobs, catalogs, and table DDL statements instead of only anonymous statement lines.
- The package version is exported from `__init__.py` and consumed by `OpenCodeSqlLanguageServer`.
- Docs/workflow consistency is machine-checked by `tests/test_docs_consistency.py`.
- The VS Code wrapper must keep its build artifacts inside `extensions/vscode-opencode-sql-lsp-wrapper/` and continue launching `opencode-sql-lsp --stdio`.

## VERIFICATION
- Unit tests live under `tests/test_*.py`; `tests/smoke_lsp_stdio.py` remains the end-to-end subprocess stdio check.
- Fast: `bash scripts/verify_fast.sh`
- Targeted:
  - `python3 -m pytest tests/test_config.py tests/test_workspace_config.py -q`
  - `python3 -m pytest tests/test_server_behaviors.py tests/test_lsp_helpers.py tests/test_diagnostics_scheduler.py -q`
  - `python3 -m pytest tests/test_sqlfluff_adapter.py -q`
  - `python3 -m pytest tests/test_docs_consistency.py -q`
  - `python3 -m pytest -m smoke -q`
- Full: `bash scripts/verify_full.sh`
- VS Code wrapper: `bash scripts/verify_vscode_wrapper.sh`

## MARKER OWNERSHIP
- `config` → `tests/test_config.py`, `tests/test_workspace_config.py`
- `server` → `tests/test_server_behaviors.py`, `tests/test_diagnostics_scheduler.py`
- `lsp_helpers` → `tests/test_lsp_helpers.py`
- `sqlfluff` → `tests/test_sqlfluff_adapter.py`
- `docs` → `tests/test_docs_consistency.py`
- `smoke` → `tests/test_smoke_stdio.py`
