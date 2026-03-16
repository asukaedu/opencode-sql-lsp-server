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

## Verify dialects

```bash
sqlfluff dialects
```
