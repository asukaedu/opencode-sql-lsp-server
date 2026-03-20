from __future__ import annotations

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

    def fake_lint_once(raw_sql: str, dialect: str) -> list[sqlfluff_adapter.SqlIssue]:
        assert dialect == "starrocks"
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

    issues = sqlfluff_adapter.lint_issues(sql, dialect="starrocks")

    assert issues == [
        sqlfluff_adapter.SqlIssue(
            code="RF02", message="real issue", line=1, character=0
        )
    ]


def test_lint_issues_filters_excluded_rules(monkeypatch: MonkeyPatch) -> None:
    def fake_lint_once(raw_sql: str, dialect: str) -> list[sqlfluff_adapter.SqlIssue]:
        assert raw_sql == "SELECT 1"
        assert dialect == "starrocks"
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
        "SELECT 1", dialect="starrocks", excluded_rules=("lt05", "ST06")
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

    def fake_lint_once(raw_sql: str, dialect: str) -> list[sqlfluff_adapter.SqlIssue]:
        assert dialect == "starrocks"
        if raw_sql == sql:
            return [prs_issue]
        assert "CROSS JOIN" not in raw_sql
        assert "UNNEST" not in raw_sql
        return sanitized_issues

    monkeypatch.setattr(sqlfluff_adapter, "_lint_once", fake_lint_once)

    issues = sqlfluff_adapter.lint_issues(sql, dialect="starrocks")

    assert issues == sanitized_issues


def test_lint_issues_sanitizes_live_lateral_unnest_parse_issue() -> None:
    if sqlfluff_adapter.get_simple_config is None or sqlfluff_adapter.Linter is None:
        pytest.skip("sqlfluff not installed")

    sql = "SELECT *\nFROM t, LATERAL UNNEST(arr) AS u(x)\n"

    issues = sqlfluff_adapter.lint_issues(sql, dialect="starrocks")

    assert all(issue.code != "PRS" for issue in issues), issues


def test_format_sql_raises_when_sqlfluff_fix_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(sqlfluff_adapter, "fix", None)
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
    def fake_fix(sql: str, *, dialect: str) -> str:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        return "SELECT 1\n\n\n"

    monkeypatch.setattr(sqlfluff_adapter, "fix", fake_fix)

    formatted = sqlfluff_adapter.format_sql("SELECT 1\n", dialect="starrocks")

    assert formatted == "SELECT 1\n"
