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

## Dialect config

Create `.opencode/sql-lsp.json` in your project:

```json
{
  "defaultDialect": "starrocks",
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
- The server also provides lightweight SQL keyword completion, hover help, code actions, and document/workspace symbols for agent-driven editing.
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
