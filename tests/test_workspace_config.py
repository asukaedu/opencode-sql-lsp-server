from __future__ import annotations

from pathlib import Path

import pytest
from pygls.exceptions import PyglsError

from opencode_sql_lsp_server.config import SqlLspConfig
from opencode_sql_lsp_server.workspace_config import (
    ConfigCacheEntry,
    best_root_for_uri,
    config_cache_entry_for_root,
)


pytestmark = pytest.mark.config


def test_best_root_for_uri_prefers_most_specific_workspace(tmp_path: Path) -> None:
    outer = tmp_path / "workspace"
    inner = outer / "nested"
    inner.mkdir(parents=True)
    target = inner / "query.sql"
    _ = target.write_text("SELECT 1\n", encoding="utf-8")

    result = best_root_for_uri([outer.resolve(), inner.resolve()], target.as_uri())

    assert result == inner.resolve()


def test_config_cache_entry_reuses_last_good_config_on_invalid_reload(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    config_dir = root / ".opencode"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "sql-lsp.json"
    _ = config_path.write_text('{"defaultDialect": "trino"}\n', encoding="utf-8")

    reported: list[str] = []

    def report(error: Exception, error_type: type[PyglsError]) -> None:
        assert error_type is PyglsError
        reported.append(str(error))

    cache: dict[Path, ConfigCacheEntry] = {}
    first = config_cache_entry_for_root(
        root.resolve(),
        config_cache=cache,
        report_server_error=report,
    )

    assert first.config.default_dialect == "trino"

    _ = config_path.write_text("{ invalid json }\n", encoding="utf-8")
    second = config_cache_entry_for_root(
        root.resolve(),
        config_cache=cache,
        report_server_error=report,
    )

    assert second.config.default_dialect == "trino"
    assert reported


def test_config_cache_entry_uses_defaults_when_config_missing(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir(parents=True)

    cache: dict[Path, ConfigCacheEntry] = {}
    entry = config_cache_entry_for_root(
        root.resolve(),
        config_cache=cache,
        report_server_error=lambda _error, _error_type: None,
    )

    assert entry == ConfigCacheEntry(mtime_ns=None, config=SqlLspConfig.default())
