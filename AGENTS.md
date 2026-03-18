# PROJECT KNOWLEDGE BASE

**Generated:** 2026-03-17T19:33:31Z
**Commit:** ae427c0
**Branch:** main

## OVERVIEW
Python stdio SQL LSP server for OpenCode. Core stack: `pygls` + `lsprotocol` + `sqlfluff`, with dialect selection from `.opencode/sql-lsp.json`.

## STRUCTURE
```text
./
├── src/opencode_sql_lsp_server/   # actual package; all server behavior lives here
├── extensions/vscode-opencode-sql-lsp-wrapper/ # isolated VS Code client wrapper
├── tests/                         # focused pytest coverage + stdio smoke test
├── scripts/                       # build + publish helpers
├── .github/workflows/             # CI validation pipeline
├── pyproject.toml                 # packaging metadata and console entrypoint
└── README.md                      # install, config, and OpenCode wiring
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| CLI entrypoint | `src/opencode_sql_lsp_server/cli.py` | `opencode-sql-lsp` script dispatches here |
| LSP lifecycle | `src/opencode_sql_lsp_server/server.py` | initialize, open/change/save, formatting |
| Dialect config loading | `src/opencode_sql_lsp_server/config.py` | reads `.opencode/sql-lsp.json` |
| sqlfluff bridge | `src/opencode_sql_lsp_server/sqlfluff_adapter.py` | lint + format wrappers |
| Packaging/install | `pyproject.toml` | src-layout package, console script |
| VS Code wrapper | `extensions/vscode-opencode-sql-lsp-wrapper/` | separate Node/TypeScript client wrapper for `opencode-sql-lsp --stdio` |
| Focused regression tests | `tests/test_*.py` | config, adapter, and server behavior coverage |
| End-to-end behavior | `tests/smoke_lsp_stdio.py` | stdio RPC smoke coverage across multiple workspaces |
| CI workflow | `.github/workflows/ci.yml` | pytest, smoke, basedpyright, packaging matrix |
| Release flow | `scripts/build_dist.sh`, `scripts/publish_pypi.sh` | build/check/upload helpers |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `main` | function | `src/opencode_sql_lsp_server/cli.py` | parses `--stdio` / optional `--workspace`, then starts IO server |
| `OpenCodeSqlLanguageServer` | class | `src/opencode_sql_lsp_server/server.py` | stores workspace roots, config cache, doc state |
| `initialize` | LSP handler | `src/opencode_sql_lsp_server/server.py` | derives workspace roots from initialize params |
| `did_open` / `did_change` / `did_save` | LSP handlers | `src/opencode_sql_lsp_server/server.py` | trigger debounced linting unless file is too large |
| `formatting` | LSP handler | `src/opencode_sql_lsp_server/server.py` | formats full document with sqlfluff |
| `completion` / `hover` / `code_action` | LSP handlers | `src/opencode_sql_lsp_server/server.py` | lightweight editor assist + fix suggestions |
| `document_symbol` / `workspace_symbol` | LSP handlers | `src/opencode_sql_lsp_server/server.py` | SQL structure discovery for editors/agents |
| `SqlLspConfig` | dataclass | `src/opencode_sql_lsp_server/config.py` | default dialect + glob overrides |
| `lint_issues` / `format_sql` | functions | `src/opencode_sql_lsp_server/sqlfluff_adapter.py` | isolate sqlfluff API calls and fallback behavior |

## CONVENTIONS
- Python uses src-layout: imports come from `src/opencode_sql_lsp_server`, not repo root.
- Console entrypoint is declared in `pyproject.toml` as `opencode-sql-lsp = "opencode_sql_lsp_server.cli:main"`.
- Only stdio transport is supported; `cli.py` exits if `--stdio` is omitted.
- The VS Code wrapper stays outside Python packaging and launches the same `opencode-sql-lsp --stdio` contract from `extensions/vscode-opencode-sql-lsp-wrapper/`.
- Workspace-specific dialect rules come from `.opencode/sql-lsp.json`; missing config falls back to `starrocks`.
- Server code prefers graceful degradation over hard crashes: config-load failures reuse cached config when possible; missing `sqlfluff` becomes surfaced diagnostics/runtime errors instead of import-time failure.
- Large files skip linting based on `_MAX_LINT_LINES` / `_MAX_LINT_BYTES` in `server.py`.
- Local and CI verification are aligned around `pytest`, the stdio smoke test, `basedpyright`, and `scripts/build_dist.sh`.

## ANTI-PATTERNS (THIS PROJECT)
- Do not add alternate transports without updating the CLI contract; current interface is explicitly stdio-only.
- Do not fold Node/VS Code wrapper files into `pyproject.toml` packaging; keep them isolated under `extensions/`.
- Do not bypass `SqlLspConfig`; dialect resolution is centralized there and consumed through `dialect_for_document()`.
- Do not call `sqlfluff` directly from new handlers; keep integration inside `sqlfluff_adapter.py`.
- Do not assume one workspace root; initialization collects multiple folders and picks the best matching root per document.
- Do not rely on committed local artifacts. `.gitignore` excludes `.venv/`, `dist/`, `build/`, and `*.egg-info/`.

## UNIQUE STYLES
- Repo name is hyphenated, package name is underscored: `opencode-sql-lsp-server` vs `opencode_sql_lsp_server`.
- Test strategy is integration-first: `tests/smoke_lsp_stdio.py` drives raw LSP JSON-RPC over subprocess stdio instead of unit-testing internals.
- Test strategy is now layered: focused pytest coverage guards module-level behavior while `tests/smoke_lsp_stdio.py` preserves end-to-end stdio behavior.
- Release scripts are shell-first, not task-runner-based.

## COMMANDS
```bash
# install from source
pipx install -e .
python3 -m pip install -e .

# run server
opencode-sql-lsp --stdio
opencode-sql-lsp --stdio --workspace /path/to/workspace

# smoke test
python3 -m pytest
python3 tests/smoke_lsp_stdio.py

# type check
python3 -m basedpyright src/opencode_sql_lsp_server

# verify VS Code wrapper
bash scripts/verify_vscode_wrapper.sh

# build distribution
scripts/build_dist.sh

# publish distribution
scripts/publish_pypi.sh

# inspect available sqlfluff dialects
sqlfluff dialects
```

## NOTES
- `scripts/build_dist.sh` reuses the current interpreter by default, installs `build` + `twine`, runs `python -m build`, then `twine check dist/*`.
- `scripts/publish_pypi.sh` expects an existing `dist/` directory and supports non-interactive PyPI uploads via `TWINE_USERNAME` / `TWINE_PASSWORD`.
- `extensions/vscode-opencode-sql-lsp-wrapper/` has its own npm-based build/package flow and must not write artifacts to repo-root `dist/` or `build/`.
- No existing repo-specific guardrail docs were found; this file is the primary maintainer guidance layer.
- Child guidance lives in `src/opencode_sql_lsp_server/AGENTS.md`.
