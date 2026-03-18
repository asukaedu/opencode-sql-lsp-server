# PACKAGE GUIDE

## OVERVIEW
Core SQL LSP implementation: CLI bootstrap, workspace/config resolution, debounced diagnostics, sqlfluff-backed formatting, and lightweight editor-assist features.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Start server process | `cli.py` | validates `--stdio`, optional workspace override |
| Add/change protocol handlers | `server.py` | all `@server.feature(...)` handlers live here |
| Change dialect selection | `config.py` | default dialect + glob override matching |
| Change lint/format integration | `sqlfluff_adapter.py` | keep third-party API usage isolated |
| Package version only | `__init__.py` | exports `__version__` |

## STRUCTURE
```text
opencode_sql_lsp_server/
├── cli.py
├── config.py
├── server.py
├── sqlfluff_adapter.py
└── __init__.py
```

## CONVENTIONS
- Keep LSP feature registration in `server.py`; current handlers are initialize, didOpen, didChange, didSave, formatting, completion, hover, codeAction, documentSymbol, and workspaceSymbol.
- Route document dialect lookup through `OpenCodeSqlLanguageServer.dialect_for_document()` so multi-root workspace behavior stays consistent.
- Preserve cache behavior in `_config_for_root()`: missing config returns defaults; invalid config reports once per mtime and can reuse cached config.
- Diagnostics are async/debounced. Reuse `_schedule_diagnostics()` and `_run_lint_and_publish()` instead of spawning ad hoc background work.
- Large-document protections matter; respect `_MAX_LINT_LINES` and `_MAX_LINT_BYTES` when adding new lint-triggering flows.
- Preserve degraded-mode behavior in request handlers: formatting and code actions return `[]` on failure, but failures must still be surfaced through `report_server_error`.

## ANTI-PATTERNS
- Do not import `sqlfluff` in `server.py`; keep vendor interaction in `sqlfluff_adapter.py`.
- Do not duplicate URI→filesystem root logic outside `_best_root_for_uri()`.
- Do not publish diagnostics with raw sqlfluff coordinates; use `_issue_range()` / `_safe_position()` for document-safe bounds.
- Do not add synchronous heavy work to open/change/save handlers; existing design offloads linting through the event loop executor.
- Do not treat formatting failures as fatal; current behavior returns `[]` on formatter failure.

## NOTES
- `OpenCodeSqlLanguageServer` uses `max_workers=2` and stores per-document pending timers/tasks in `_doc_state`.
- Formatting replaces the full document with one `TextEdit`.
- Default dialect is `starrocks`; overrides use glob-style path matching with normalized `/` separators.
- The package version is exported from `__init__.py` and consumed by `OpenCodeSqlLanguageServer`.

## VERIFICATION
- Unit tests live under `tests/test_*.py`; `tests/smoke_lsp_stdio.py` remains the end-to-end subprocess stdio check.
- Local verification commands:
  - `python3 -m pytest`
  - `python3 tests/smoke_lsp_stdio.py`
  - `python3 -m basedpyright src/opencode_sql_lsp_server`
  - `bash scripts/build_dist.sh`
