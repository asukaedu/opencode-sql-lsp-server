from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from lsprotocol.types import Position, Range

from .sqlfluff_adapter import SqlIssue


class TextDocumentLike(Protocol):
    @property
    def source(self) -> str: ...

    @property
    def lines(self) -> Sequence[str]: ...


def safe_position(doc: TextDocumentLike, line_1: int, character: int) -> Position:
    if not doc.lines:
        return Position(line=0, character=0)
    max_line = max(0, len(doc.lines) - 1)
    line_idx = min(max(0, line_1 - 1), max_line)
    line_text = doc.lines[line_idx]
    char_idx = min(max(0, character), len(line_text))
    return Position(line=line_idx, character=char_idx)


def issue_range(doc: TextDocumentLike, issue: SqlIssue) -> Range:
    start = safe_position(doc, issue.line, issue.character)
    end_char = start.character + 1
    if doc.lines:
        line_text = doc.lines[start.line]
        end_char = min(end_char, len(line_text))
    end = Position(line=start.line, character=end_char)
    return Range(start=start, end=end)


def full_document_range(doc: TextDocumentLike) -> Range:
    last_line = max(0, len(doc.lines) - 1)
    last_char = len(doc.lines[last_line]) if doc.lines else 0
    return Range(
        start=Position(line=0, character=0),
        end=Position(line=last_line, character=last_char),
    )


def word_range_at_position(doc: TextDocumentLike, position: Position) -> Range | None:
    if not doc.lines:
        return None
    if position.line < 0 or position.line >= len(doc.lines):
        return None
    line_text = doc.lines[position.line]
    if not line_text:
        return None

    char = min(max(position.character, 0), len(line_text))
    start = char
    while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == "_"):
        start -= 1

    end = char
    while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
        end += 1

    if start == end:
        return None

    return Range(
        start=Position(line=position.line, character=start),
        end=Position(line=position.line, character=end),
    )


def word_at_position(
    doc: TextDocumentLike, position: Position
) -> tuple[str, Range] | None:
    word_range = word_range_at_position(doc, position)
    if word_range is None:
        return None
    line_text = doc.lines[position.line]
    token = line_text[word_range.start.character : word_range.end.character].strip()
    if not token:
        return None
    return token.upper(), word_range
