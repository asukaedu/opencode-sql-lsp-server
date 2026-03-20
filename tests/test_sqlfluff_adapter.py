from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from opencode_sql_lsp_server import sqlfluff_adapter


pytestmark = pytest.mark.sqlfluff


def test_lint_issues_reports_missing_sqlfluff(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sqlfluff_adapter, "get_simple_config", None)
    monkeypatch.setattr(sqlfluff_adapter, "Linter", None)
    monkeypatch.setattr(
        sqlfluff_adapter, "_sqlfluff_import_error", RuntimeError("sqlfluff boom")
    )

    issues = sqlfluff_adapter.lint_issues("SELECT 1", dialect="starrocks")

    assert len(issues) == 1
    assert issues[0].code is None
    assert "sqlfluff not available" in issues[0].message


def test_lint_issues_filters_starrocks_false_positives(
    monkeypatch: MonkeyPatch,
) -> None:
    sql = "SELECT *\nFROM t, LATERAL UNNEST(arr) AS u(x)\n"
    span_start = sql.index("AS u(x)")
    span_end = sql.index(")", span_start) + 1
    spans: list[object] = [
        type("SpanLike", (), {"start": span_start, "end": span_end})()
    ]
    prs_issue = sqlfluff_adapter.SqlIssue(
        code="PRS", message="parse", line=2, character=33
    )
    sanitized_issues = [
        sqlfluff_adapter.SqlIssue(
            code="AL01", message="alias false positive", line=2, character=33
        ),
        sqlfluff_adapter.SqlIssue(
            code="LT01",
            message="Unexpected whitespace before start bracket",
            line=2,
            character=33,
        ),
        sqlfluff_adapter.SqlIssue(
            code="RF02", message="real issue", line=1, character=0
        ),
    ]

    def fake_lint_once(
        raw_sql: str, dialect: str, file_path: str | None = None
    ) -> list[sqlfluff_adapter.SqlIssue]:
        assert dialect == "starrocks"
        assert file_path == "/tmp/query.sql"
        if raw_sql == sql:
            return [prs_issue]
        return sanitized_issues

    monkeypatch.setattr(
        sqlfluff_adapter,
        "_lint_once",
        fake_lint_once,
    )

    def fake_find_spans(raw_sql: str) -> list[object]:
        assert raw_sql == sql
        return spans

    monkeypatch.setattr(
        sqlfluff_adapter, "_find_starrocks_alias_column_list_spans", fake_find_spans
    )

    issues = sqlfluff_adapter.lint_issues(
        sql, dialect="starrocks", file_path="/tmp/query.sql"
    )

    assert issues == [
        sqlfluff_adapter.SqlIssue(
            code="RF02", message="real issue", line=1, character=0
        )
    ]


def test_lint_issues_filters_excluded_rules(monkeypatch: MonkeyPatch) -> None:
    def fake_lint_once(
        raw_sql: str, dialect: str, file_path: str | None = None
    ) -> list[sqlfluff_adapter.SqlIssue]:
        assert raw_sql == "SELECT 1"
        assert dialect == "starrocks"
        assert file_path == "/tmp/query.sql"
        return [
            sqlfluff_adapter.SqlIssue(
                code="LT05", message="line too long", line=1, character=0
            ),
            sqlfluff_adapter.SqlIssue(
                code="ST06", message="select order", line=1, character=1
            ),
            sqlfluff_adapter.SqlIssue(
                code="RF02", message="keep me", line=1, character=2
            ),
        ]

    monkeypatch.setattr(sqlfluff_adapter, "_lint_once", fake_lint_once)

    issues = sqlfluff_adapter.lint_issues(
        "SELECT 1",
        dialect="starrocks",
        excluded_rules=("lt05", "ST06"),
        file_path="/tmp/query.sql",
    )

    assert issues == [
        sqlfluff_adapter.SqlIssue(code="RF02", message="keep me", line=1, character=2)
    ]


def test_lint_issues_sanitizes_cross_join_table_function_patterns(
    monkeypatch: MonkeyPatch,
) -> None:
    sql = "SELECT *\nFROM t\nCROSS JOIN UNNEST(arr) AS u(x)\n"
    prs_issue = sqlfluff_adapter.SqlIssue(
        code="PRS", message="parse", line=3, character=11
    )
    sanitized_issues = [
        sqlfluff_adapter.SqlIssue(
            code="RF02", message="real issue", line=1, character=0
        )
    ]

    def fake_lint_once(
        raw_sql: str, dialect: str, file_path: str | None = None
    ) -> list[sqlfluff_adapter.SqlIssue]:
        assert dialect == "starrocks"
        assert file_path == "/tmp/query.sql"
        if raw_sql == sql:
            return [prs_issue]
        assert "CROSS JOIN" not in raw_sql
        assert "UNNEST" not in raw_sql
        return sanitized_issues

    monkeypatch.setattr(sqlfluff_adapter, "_lint_once", fake_lint_once)

    issues = sqlfluff_adapter.lint_issues(
        sql, dialect="starrocks", file_path="/tmp/query.sql"
    )

    assert issues == sanitized_issues


def test_lint_issues_sanitizes_live_lateral_unnest_parse_issue() -> None:
    if (
        getattr(sqlfluff_adapter, "get_simple_config") is None
        or getattr(sqlfluff_adapter, "Linter") is None
    ):
        pytest.skip("sqlfluff not installed")

    sql = "SELECT *\nFROM t, LATERAL UNNEST(arr) AS u(x)\n"

    issues = sqlfluff_adapter.lint_issues(sql, dialect="starrocks")

    assert all(issue.code != "PRS" for issue in issues), issues


def test_config_for_sql_uses_local_config_when_file_path_present(
    tmp_path: Path,
) -> None:
    if getattr(sqlfluff_adapter, "FluffConfig") is None:
        pytest.skip("sqlfluff not installed")

    _ = (tmp_path / ".sqlfluff").write_text(
        "[sqlfluff]\nexclude_rules = LT09\ndialect = starrocks\n",
        encoding="utf-8",
    )
    sql_path = tmp_path / "query.sql"
    _ = sql_path.write_text("SELECT\n    x AS y\nFROM t\n", encoding="utf-8")

    config = sqlfluff_adapter._config_for_sql("starrocks", str(sql_path))

    assert config is not None
    assert config.get("dialect") == "starrocks"
    assert config.get("exclude_rules") == "LT09"


def test_format_sql_raises_when_sqlfluff_fix_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(sqlfluff_adapter, "get_simple_config", None)
    monkeypatch.setattr(sqlfluff_adapter, "FluffConfig", None)
    monkeypatch.setattr(
        sqlfluff_adapter, "_sqlfluff_import_error", RuntimeError("formatter missing")
    )

    try:
        _ = sqlfluff_adapter.format_sql("SELECT 1", dialect="starrocks")
    except RuntimeError as exc:
        assert "sqlfluff not available" in str(exc)
    else:
        raise AssertionError("format_sql should fail when fix is unavailable")


def test_format_sql_normalizes_trailing_blank_lines(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, bool, str | None]] = []

    class FakeFixedFile:
        def fix_string(self) -> tuple[str, bool]:
            return ("SELECT 1\n\n\n", True)

    class FakePath:
        files = [FakeFixedFile()]

    class FakeResult:
        paths = [FakePath()]

        def count_tmp_prs_errors(self) -> tuple[int, int]:
            return (0, 0)

    class FakeConfig:
        def get(self, key: str):
            assert key == "fix_even_unparsable"
            return False

    class FakeLinter:
        def __init__(self, *, config: object) -> None:
            assert config is fake_config

        def lint_string_wrapped(
            self,
            sql: str,
            fname: str = "<string input>",
            fix: bool = False,
            stdin_filename: str | None = None,
        ) -> FakeResult:
            calls.append((sql, fix, stdin_filename))
            assert fname == "stdin"
            return FakeResult()

    fake_config = FakeConfig()
    monkeypatch.setattr(sqlfluff_adapter, "Linter", FakeLinter)
    monkeypatch.setattr(
        sqlfluff_adapter,
        "_config_for_sql",
        lambda dialect, file_path: fake_config,
    )

    formatted = sqlfluff_adapter.format_sql(
        "SELECT 1\n", dialect="starrocks", file_path="/tmp/query.sql"
    )

    assert formatted == "SELECT 1\n"
    assert calls == [("SELECT 1\n", True, "/tmp/query.sql")]


def test_lint_issues_uses_path_config_when_simple_config_is_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    class FakeViolation:
        line_no = 1
        line_pos = 1

        @staticmethod
        def rule_code() -> str:
            return "LT09"

        @staticmethod
        def desc() -> str:
            return "line issue"

    class FakeResult:
        @staticmethod
        def get_violations() -> list[FakeViolation]:
            return [FakeViolation()]

    class FakeLinter:
        def __init__(self, *, config: object) -> None:
            assert config == "path-config"

        def lint_string_wrapped(
            self,
            sql: str,
            fname: str = "<string input>",
            stdin_filename: str | None = None,
        ) -> FakeResult:
            assert sql == "SELECT 1"
            assert fname == "stdin"
            assert stdin_filename == "/tmp/query.sql"
            return FakeResult()

    monkeypatch.setattr(sqlfluff_adapter, "get_simple_config", None)
    monkeypatch.setattr(sqlfluff_adapter, "Linter", FakeLinter)
    monkeypatch.setattr(
        sqlfluff_adapter,
        "_config_for_sql",
        lambda dialect, file_path: "path-config",
    )

    issues = sqlfluff_adapter.lint_issues(
        "SELECT 1", dialect="starrocks", file_path="/tmp/query.sql"
    )

    assert issues == [
        sqlfluff_adapter.SqlIssue(
            code="LT09", message="line issue", line=1, character=0
        )
    ]
