from __future__ import annotations

import json
from pathlib import Path

import pytest

from opencode_sql_lsp_server.config import SqlLspConfig, SqlLspConfigLoadError


pytestmark = pytest.mark.config


def test_load_returns_defaults_when_config_missing(tmp_path: Path) -> None:
    config = SqlLspConfig.load(tmp_path)

    assert config == SqlLspConfig.default()


def test_load_raises_for_invalid_json(tmp_path: Path) -> None:
    config_dir = tmp_path / ".opencode"
    config_dir.mkdir()
    _ = (config_dir / "sql-lsp.json").write_text("{not valid json}\n", encoding="utf-8")

    with pytest.raises(SqlLspConfigLoadError):
        _ = SqlLspConfig.load(tmp_path)


def test_load_applies_overrides_and_positive_limits(tmp_path: Path) -> None:
    config_dir = tmp_path / ".opencode"
    config_dir.mkdir()
    _ = (config_dir / "sql-lsp.json").write_text(
        json.dumps(
            {
                "defaultDialect": "trino",
                "overrides": {
                    "queries\\*.sql": "starrocks",
                    "": "ignored",
                    "bad": 42,
                },
                "maxLintLines": 10,
                "maxLintBytes": 2048,
                "excludedRules": ["lt05", "ST06", "", 42],
            }
        ),
        encoding="utf-8",
    )

    config = SqlLspConfig.load(tmp_path)

    assert config.default_dialect == "trino"
    assert config.dialect_for_path("queries/example.sql") == "starrocks"
    assert config.dialect_for_path("other/example.sql") == "trino"
    assert config.max_lint_lines == 10
    assert config.max_lint_bytes == 2048
    assert config.excluded_rules == ("LT05", "ST06")


def test_load_falls_back_for_blank_dialect_and_non_positive_limits(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".opencode"
    config_dir.mkdir()
    _ = (config_dir / "sql-lsp.json").write_text(
        json.dumps(
            {
                "defaultDialect": "   ",
                "maxLintLines": 0,
                "maxLintBytes": -1,
            }
        ),
        encoding="utf-8",
    )

    config = SqlLspConfig.load(tmp_path)
    defaults = SqlLspConfig.default()

    assert config.default_dialect == defaults.default_dialect
    assert config.max_lint_lines == defaults.max_lint_lines
    assert config.max_lint_bytes == defaults.max_lint_bytes
    assert config.excluded_rules == defaults.excluded_rules
