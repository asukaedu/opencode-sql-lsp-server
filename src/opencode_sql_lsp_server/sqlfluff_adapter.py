from __future__ import annotations

from dataclasses import dataclass


try:
    from sqlfluff.api.simple import fix, get_simple_config
    from sqlfluff.core import Linter
except Exception as e:
    fix = None
    get_simple_config = None
    Linter = None
    _sqlfluff_import_error = e
else:
    _sqlfluff_import_error = None


@dataclass(frozen=True)
class SqlIssue:
    message: str
    line: int
    character: int


def lint_issues(sql: str, dialect: str) -> list[SqlIssue]:
    if get_simple_config is None or Linter is None:
        return [
            SqlIssue(
                message=f"sqlfluff not available: {_sqlfluff_import_error}",
                line=1,
                character=0,
            )
        ]

    try:
        config = get_simple_config(dialect=dialect)
        linter = Linter(config=config)
        result = linter.lint_string_wrapped(sql)
        violations = result.get_violations()
        issues: list[SqlIssue] = []
        for v in violations:
            line_no = getattr(v, "line_no", None)
            line_pos = getattr(v, "line_pos", None)
            if isinstance(line_no, int) and isinstance(line_pos, int):
                issues.append(
                    SqlIssue(
                        message=str(
                            getattr(v, "desc", None)
                            or getattr(v, "description", None)
                            or v
                        ),
                        line=max(1, line_no),
                        character=max(0, line_pos - 1),
                    )
                )
            else:
                issues.append(SqlIssue(message=str(v), line=1, character=0))
        return issues
    except Exception as e:
        return [SqlIssue(message=str(e), line=1, character=0)]


def format_sql(sql: str, dialect: str) -> str:
    if fix is None:
        raise RuntimeError(f"sqlfluff not available: {_sqlfluff_import_error}")

    return fix(sql, dialect=dialect)
