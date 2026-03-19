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
    code: str | None
    message: str
    line: int
    character: int


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


_STARROCKS_TABLE_FUNCTIONS = ("UNNEST", "JSON_EACH")
_STARROCKS_SANITIZED_FALSE_POSITIVE_CODES = frozenset({"AL01", "AL05", "CP02", "RF04"})
_STARROCKS_SANITIZED_LT01_FRAGMENTS = (
    "Expected single whitespace between naked identifier and start bracket",
    "Unexpected whitespace before start bracket",
    "Unnecessary trailing whitespace.",
)


def _violation_code(violation: object) -> str | None:
    rule_code = getattr(violation, "rule_code", None)
    if callable(rule_code):
        resolved = rule_code()
        return resolved if isinstance(resolved, str) and resolved else None
    return rule_code if isinstance(rule_code, str) and rule_code else None


def _violation_message(violation: object) -> str:
    for attribute in ("desc", "description"):
        candidate = getattr(violation, attribute, None)
        if callable(candidate):
            resolved = candidate()
            if isinstance(resolved, str) and resolved:
                return resolved
        elif isinstance(candidate, str) and candidate:
            return candidate
    return str(violation)


def _to_issue(violation: object) -> SqlIssue:
    line_no = getattr(violation, "line_no", None)
    line_pos = getattr(violation, "line_pos", None)
    if isinstance(line_no, int) and isinstance(line_pos, int):
        return SqlIssue(
            code=_violation_code(violation),
            message=_violation_message(violation),
            line=max(1, line_no),
            character=max(0, line_pos - 1),
        )
    return SqlIssue(
        code=_violation_code(violation),
        message=_violation_message(violation),
        line=1,
        character=0,
    )


def _lint_once(sql: str, dialect: str) -> list[SqlIssue]:
    if get_simple_config is None or Linter is None:
        return [
            SqlIssue(
                code=None,
                message=f"sqlfluff not available: {_sqlfluff_import_error}",
                line=1,
                character=0,
            )
        ]

    config = get_simple_config(dialect=dialect)
    linter = Linter(config=config)
    result = linter.lint_string_wrapped(sql)
    return [_to_issue(violation) for violation in result.get_violations()]


def _mask_sql_for_detection(sql: str) -> str:
    masked: list[str] = []
    i = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    while i < len(sql):
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if in_line_comment:
            masked.append("\n" if char == "\n" else " ")
            if char == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            masked.append("\n" if char == "\n" else " ")
            if char == "*" and nxt == "/":
                masked.append(" ")
                i += 2
                in_block_comment = False
            else:
                i += 1
            continue
        if in_single:
            masked.append("\n" if char == "\n" else " ")
            if char == "'":
                if nxt == "'":
                    masked.append(" ")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            masked.append("\n" if char == "\n" else " ")
            if char == '"':
                in_double = False
            i += 1
            continue
        if char == "-" and nxt == "-":
            masked.extend((" ", " "))
            in_line_comment = True
            i += 2
            continue
        if char == "/" and nxt == "*":
            masked.extend((" ", " "))
            in_block_comment = True
            i += 2
            continue
        if char == "'":
            masked.append(" ")
            in_single = True
            i += 1
            continue
        if char == '"':
            masked.append(" ")
            in_double = True
            i += 1
            continue
        masked.append(char)
        i += 1
    return "".join(masked)


def _skip_whitespace(sql: str, start: int) -> int:
    index = start
    while index < len(sql) and sql[index].isspace():
        index += 1
    return index


def _match_keyword(sql: str, start: int, keyword: str) -> int | None:
    end = start + len(keyword)
    if sql[start:end].casefold() != keyword.casefold():
        return None
    if start > 0 and (sql[start - 1].isalnum() or sql[start - 1] == "_"):
        return None
    if end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
        return None
    return end


def _find_matching_paren(sql: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(sql)):
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_clause_start(masked: str, start: int) -> int:
    cursor = start
    while cursor > 0 and masked[cursor - 1].isspace():
        cursor -= 1
    for keyword in ("LATERAL", "CROSS JOIN"):
        keyword_len = len(keyword)
        clause_start = cursor - keyword_len
        if clause_start < 0:
            continue
        if masked[clause_start:cursor].casefold() == keyword.casefold():
            if clause_start > 0 and (
                masked[clause_start - 1].isalnum() or masked[clause_start - 1] == "_"
            ):
                continue
            return clause_start
    return start


def _find_identifier_end(sql: str, start: int) -> int | None:
    if start >= len(sql):
        return None
    char = sql[start]
    if not (char.isalpha() or char == "_"):
        return None
    end = start + 1
    while end < len(sql) and (sql[end].isalnum() or sql[end] in {"_", "$"}):
        end += 1
    return end


def _find_starrocks_alias_column_list_spans(sql: str) -> list[_Span]:
    masked = _mask_sql_for_detection(sql)
    spans: list[_Span] = []
    index = 0
    while index < len(masked):
        relation_start: int | None = None
        cursor = index
        lateral_end = _match_keyword(masked, index, "LATERAL")
        if lateral_end is not None:
            relation_start = index
            cursor = _skip_whitespace(masked, lateral_end)
        else:
            cross_end = _match_keyword(masked, index, "CROSS")
            if cross_end is None:
                index += 1
                continue
            cursor = _skip_whitespace(masked, cross_end)
            join_end = _match_keyword(masked, cursor, "JOIN")
            if join_end is None:
                index += 1
                continue
            relation_start = index
            cursor = _skip_whitespace(masked, join_end)
        if relation_start is None:
            index += 1
            continue
        function_end: int | None = None
        for function_name in _STARROCKS_TABLE_FUNCTIONS:
            function_end = _match_keyword(masked, cursor, function_name)
            if function_end is not None:
                break
        if function_end is None:
            index += 1
            continue
        cursor = _skip_whitespace(masked, function_end)
        if cursor >= len(masked) or masked[cursor] != "(":
            index += 1
            continue
        function_close = _find_matching_paren(masked, cursor)
        if function_close is None:
            index += 1
            continue
        cursor = _skip_whitespace(masked, function_close + 1)
        span_start = _find_clause_start(masked, relation_start)
        as_end = _match_keyword(masked, cursor, "AS")
        if as_end is not None:
            cursor = _skip_whitespace(masked, as_end)
        alias_end = _find_identifier_end(masked, cursor)
        if alias_end is None:
            spans.append(_Span(start=span_start, end=function_close + 1))
            index = function_close + 1
            continue
        cursor = _skip_whitespace(masked, alias_end)
        if cursor >= len(masked) or masked[cursor] != "(":
            spans.append(_Span(start=span_start, end=alias_end))
            index = alias_end
            continue
        alias_close = _find_matching_paren(masked, cursor)
        if alias_close is None:
            index += 1
            continue
        spans.append(_Span(start=span_start, end=alias_close + 1))
        index = alias_close + 1
    return spans


def _sanitize_sql(sql: str, spans: list[_Span]) -> str:
    chars = list(sql)
    for span in spans:
        for index in range(span.start, span.end):
            if not chars[index].isspace():
                chars[index] = " "
    return "".join(chars)


def _has_prs_issue(issues: list[SqlIssue]) -> bool:
    return any(issue.code == "PRS" for issue in issues)


def _issue_offset(sql: str, issue: SqlIssue) -> int | None:
    if issue.line < 1 or issue.character < 0:
        return None
    lines = sql.splitlines(keepends=True)
    if issue.line > len(lines):
        return None
    line_text = lines[issue.line - 1]
    if issue.character > len(line_text.rstrip("\r\n")):
        return None
    return sum(len(line) for line in lines[: issue.line - 1]) + issue.character


def _is_adjacent_to_span(offset: int, span: _Span) -> bool:
    return span.start <= offset <= span.end + 1


def _prs_issue_targets_span(
    sql: str, issues: list[SqlIssue], spans: list[_Span]
) -> bool:
    for issue in issues:
        if issue.code != "PRS":
            continue
        offset = _issue_offset(sql, issue)
        if offset is None:
            continue
        if any(_is_adjacent_to_span(offset, span) for span in spans):
            return True
    return False


def _line_overlaps_span(sql: str, line_no: int, spans: list[_Span]) -> bool:
    offset = _issue_offset(
        sql, SqlIssue(code=None, message="", line=line_no, character=0)
    )
    if offset is None:
        return False
    line_end = sql.find("\n", offset)
    if line_end == -1:
        line_end = len(sql)
    return any(span.start <= line_end and offset <= span.end for span in spans)


def _filter_sanitized_starrocks_issues(
    sql: str, issues: list[SqlIssue], spans: list[_Span]
) -> list[SqlIssue]:
    filtered: list[SqlIssue] = []
    for issue in issues:
        offset = _issue_offset(sql, issue)
        if offset is not None and any(
            _is_adjacent_to_span(offset, span) for span in spans
        ):
            continue
        if not _line_overlaps_span(sql, issue.line, spans):
            filtered.append(issue)
            continue
        if issue.code in _STARROCKS_SANITIZED_FALSE_POSITIVE_CODES:
            continue
        if issue.code == "LT01" and any(
            fragment in issue.message
            for fragment in _STARROCKS_SANITIZED_LT01_FRAGMENTS
        ):
            continue
        if issue.code == "LT12":
            continue
        filtered.append(issue)
    return filtered


def _filter_excluded_rules(
    issues: list[SqlIssue], excluded_rules: frozenset[str]
) -> list[SqlIssue]:
    if not excluded_rules:
        return issues
    return [issue for issue in issues if issue.code not in excluded_rules]


def _normalize_formatted_sql(sql: str) -> str:
    if not sql:
        return sql
    normalized = sql.rstrip("\r\n")
    if not normalized:
        return "\n"
    return f"{normalized}\n"


def lint_issues(
    sql: str, dialect: str, *, excluded_rules: tuple[str, ...] = ()
) -> list[SqlIssue]:
    excluded = frozenset(
        rule.strip().upper() for rule in excluded_rules if rule.strip()
    )
    try:
        issues = _lint_once(sql, dialect)
        if dialect.casefold() != "starrocks" or not _has_prs_issue(issues):
            return _filter_excluded_rules(issues, excluded)
        spans = _find_starrocks_alias_column_list_spans(sql)
        if not spans or not _prs_issue_targets_span(sql, issues, spans):
            return _filter_excluded_rules(issues, excluded)
        sanitized_issues = _lint_once(_sanitize_sql(sql, spans), dialect)
        if _has_prs_issue(sanitized_issues):
            return _filter_excluded_rules(issues, excluded)
        return _filter_excluded_rules(
            _filter_sanitized_starrocks_issues(sql, sanitized_issues, spans),
            excluded,
        )
    except Exception as e:
        return [SqlIssue(code=None, message=str(e), line=1, character=0)]


def format_sql(sql: str, dialect: str) -> str:
    if fix is None:
        raise RuntimeError(f"sqlfluff not available: {_sqlfluff_import_error}")

    return _normalize_formatted_sql(fix(sql, dialect=dialect))
