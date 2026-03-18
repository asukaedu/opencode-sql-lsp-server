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
python3 -m pytest
python3 tests/smoke_lsp_stdio.py
python3 -m basedpyright src/opencode_sql_lsp_server
bash scripts/build_dist.sh
```

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
