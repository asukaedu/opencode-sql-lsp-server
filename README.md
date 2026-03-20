# opencode-sql-lsp-server

SQL LSP server (stdio) intended for OpenCode, with dialect-aware parsing/formatting.

## Install

### From PyPI (recommended)

```bash
pipx install opencode-sql-lsp-server
```

### From source

Recommended: pipx (or a venv)

```bash
pipx install -e .
```

If you don't have pipx, use a virtualenv or `uv tool install`.

Or pip:

```bash
python3 -m pip install -e .
```

## OpenCode config

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "lsp": {
    "opencode-sql": {
      "command": ["opencode-sql-lsp", "--stdio"],
      "extensions": [".sql"]
    }
  }
}
```

## VS Code wrapper extension

This repository now also carries a minimal VS Code wrapper extension under:

```text
extensions/vscode-opencode-sql-lsp-wrapper/
```

The wrapper is a thin frontend only. It launches the existing Python server with:

```bash
opencode-sql-lsp --stdio
```

Build and package the wrapper locally with:

```bash
cd extensions/vscode-opencode-sql-lsp-wrapper
npm install
npm run verify
```

If `opencode-sql-lsp` is not on `PATH`, configure `opencodeSql.serverPath` in VS Code settings.

## Dialect config

Create `.opencode/sql-lsp.json` in your project:

```json
{
  "defaultDialect": "starrocks",
  "excludedRules": ["LT05", "ST06"],
  "overrides": {
    "trino/**/*.sql": "trino"
  }
}
```

Dialect keys are sqlfluff dialect labels, e.g.:

- `trino`
- `starrocks`

## Notes

- Diagnostics and formatting are powered by `sqlfluff`.
- Dialect keys (sqlfluff): `trino`, `starrocks`.
- By default, style-only sqlfluff rules `LT05` (long lines) and `ST06` (select target ordering) are excluded to avoid noisy diagnostics for practical StarRocks queries. Override with `.opencode/sql-lsp.json` if you want a different policy.
- The server also provides lightweight SQL keyword completion, hover help, code actions, and document/workspace symbols for agent-driven editing.
- When `defaultDialect` resolves to `starrocks`, completion and hover include StarRocks-oriented phrases such as `CREATE MATERIALIZED VIEW`, `CREATE ROUTINE LOAD`, `CREATE CATALOG`, `INSERT OVERWRITE`, `PARTITION BY`, `DISTRIBUTED BY`, `UNNEST`, and `JSON_EACH`.
- Document/workspace symbols recognize named StarRocks entities like materialized views, routine load jobs, catalogs, and common DDL statements, which makes multi-file agent navigation less blind than line-only statement symbols.
- Formatting and code-action failures degrade safely to empty results and are reported through the server error channel.

## Optional performance controls

You can tune large-file lint skipping per workspace:

```json
{
  "defaultDialect": "starrocks",
  "maxLintLines": 5000,
  "maxLintBytes": 200000
}
```

When a document exceeds either limit, linting is skipped and the server publishes a warning diagnostic instead.

## StarRocks-focused coverage

The server is still intentionally lightweight, but it now covers more of the StarRocks workflow surface that matters for agent-driven editing:

- lint/format delegation through `sqlfluff` with StarRocks as the degraded default dialect
- false-positive mitigation for `LATERAL UNNEST(...) AS alias(col)` and `LATERAL JSON_EACH(...) AS alias(k, v)` patterns
- StarRocks-aware completion/hover entries for materialized views, routine load, catalogs, partitioning/distribution clauses, and table functions
- named document/workspace symbols for `CREATE MATERIALIZED VIEW`, `CREATE ROUTINE LOAD`, `CREATE CATALOG`, `CREATE/ALTER/DROP TABLE`, and related statements

This is not a full StarRocks parser or semantic engine. It is a practical LSP layer tuned to be useful for SQL authoring agents without pretending to cover the entire StarRocks product surface.

## Verify dialects

```bash
sqlfluff dialects
```

## Contributor workflow

Install the editable package with dev dependencies:

```bash
python3 -m pip install -e ".[dev]"
```

Run the local verification gates before opening a PR:

```bash
bash scripts/verify_fast.sh
bash scripts/verify_full.sh
```

### Verification ladder

- Fast — default loop for small code changes:
  - `bash scripts/verify_fast.sh`
- Targeted — when only one area changed:
  - Config/cache work: `python3 -m pytest tests/test_config.py tests/test_workspace_config.py -q`
  - Server behavior work: `python3 -m pytest tests/test_server_behaviors.py tests/test_lsp_helpers.py -q`
  - Diagnostics scheduling work: `python3 -m pytest tests/test_server_behaviors.py tests/test_diagnostics_scheduler.py -q`
  - sqlfluff adapter work: `python3 -m pytest tests/test_sqlfluff_adapter.py -q`
  - Docs/workflow consistency: `python3 -m pytest tests/test_docs_consistency.py -q`
  - Smoke-only: `python3 -m pytest -m smoke -q`
- Full — before handoff/PR:
  - `bash scripts/verify_full.sh`
- VS Code wrapper — when changing `extensions/vscode-opencode-sql-lsp-wrapper/**`:
  - `bash scripts/verify_vscode_wrapper.sh`

### Marker-driven verification lanes

- `@pytest.mark.config`
  - Owns: `tests/test_config.py`, `tests/test_workspace_config.py`
  - Use when changing `config.py` or `workspace_config.py`
- `@pytest.mark.server`
  - Owns: `tests/test_server_behaviors.py`, `tests/test_diagnostics_scheduler.py`
  - Use when changing `server.py` or diagnostics scheduling behavior
- `@pytest.mark.lsp_helpers`
  - Owns: `tests/test_lsp_helpers.py`
  - Use when changing `lsp_utils.py` or `symbol_provider.py`
- `@pytest.mark.sqlfluff`
  - Owns: `tests/test_sqlfluff_adapter.py`
  - Use when changing `sqlfluff_adapter.py`
- `@pytest.mark.docs`
  - Owns: `tests/test_docs_consistency.py`
  - Use when changing `README.md`, `src/opencode_sql_lsp_server/AGENTS.md`, verification scripts, or `.github/workflows/ci.yml`
- `@pytest.mark.smoke`
  - Owns: `tests/test_smoke_stdio.py`
  - Use before handoff when transport or end-to-end behavior may be affected

### Multi-agent handoff guide

- Handler changes in `src/opencode_sql_lsp_server/server.py`
  - Also inspect: `src/opencode_sql_lsp_server/lsp_utils.py`, `src/opencode_sql_lsp_server/symbol_provider.py`, `src/opencode_sql_lsp_server/diagnostics_scheduler.py`
  - Do not change: CLI transport contract in `cli.py`
  - Verify: `python3 -m pytest tests/test_server_behaviors.py tests/test_lsp_helpers.py tests/test_diagnostics_scheduler.py -q`
- Dialect/config changes in `src/opencode_sql_lsp_server/config.py` or `src/opencode_sql_lsp_server/workspace_config.py`
  - Also inspect: workspace-root precedence and cached config fallback behavior
  - Do not change: default degraded fallback to `starrocks`
  - Verify: `python3 -m pytest tests/test_config.py tests/test_workspace_config.py -q`
- sqlfluff integration changes in `src/opencode_sql_lsp_server/sqlfluff_adapter.py`
  - Do not import `sqlfluff` directly into `server.py`
  - Verify: `python3 -m pytest tests/test_sqlfluff_adapter.py tests/test_server_behaviors.py -q`
- Diagnostics scheduling changes in `src/opencode_sql_lsp_server/diagnostics_scheduler.py`
  - Also inspect: cancellation/version behavior in `src/opencode_sql_lsp_server/server.py`
  - Do not change: debounce semantics for didOpen/didChange/didSave
  - Verify: `python3 -m pytest tests/test_server_behaviors.py tests/test_diagnostics_scheduler.py -q`
- Release/build workflow changes in `scripts/` or `.github/workflows/`
  - Verify: `python3 -m pytest tests/test_docs_consistency.py -q && bash scripts/build_dist.sh`
- VS Code wrapper changes in `extensions/vscode-opencode-sql-lsp-wrapper/`
  - Do not change: `pyproject.toml` packaging boundaries or the `opencode-sql-lsp --stdio` server contract
  - Verify: `bash scripts/verify_vscode_wrapper.sh && bash scripts/verify_fast.sh`

Docs-only changes now take a lighter CI path: `.github/workflows/ci.yml` always runs `quick-validate`, but the full matrix job only runs when code-affecting files change. Docs-only changes still run `python -m pytest tests/test_docs_consistency.py -q` in CI.

The GitHub Actions workflow in `.github/workflows/ci.yml` runs the same checks on Python 3.10, 3.11, and 3.12.

## Release workflow

Build and validate the distribution:

```bash
bash scripts/build_dist.sh
```

Upload to PyPI with a token supplied via environment variable:

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-***
bash scripts/publish_pypi.sh
```

If `TWINE_PASSWORD` is unset and the script is run in an interactive terminal, it will prompt for the token. In CI or other non-interactive environments, the token must be provided via environment variables.
