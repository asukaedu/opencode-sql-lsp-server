from __future__ import annotations

import re

from lsprotocol.types import DocumentSymbol, Position, Range, SymbolKind

from .lsp_utils import TextDocumentLike, full_document_range


_NAMED_STATEMENT_PATTERNS: tuple[tuple[re.Pattern[str], SymbolKind, str], ...] = (
    (
        re.compile(
            r"^\s*CREATE\s+MATERIALIZED\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[`\"\w.$-]+)",
            re.IGNORECASE,
        ),
        SymbolKind.Interface,
        "Materialized view",
    ),
    (
        re.compile(
            r"^\s*REFRESH\s+MATERIALIZED\s+VIEW\s+(?P<name>[`\"\w.$-]+)",
            re.IGNORECASE,
        ),
        SymbolKind.Event,
        "Refresh materialized view",
    ),
    (
        re.compile(
            r"^\s*CREATE\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[`\"\w.$-]+)",
            re.IGNORECASE,
        ),
        SymbolKind.Interface,
        "View",
    ),
    (
        re.compile(
            r"^\s*CREATE\s+(?:EXTERNAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[`\"\w.$-]+)",
            re.IGNORECASE,
        ),
        SymbolKind.Class,
        "Table",
    ),
    (
        re.compile(r"^\s*ALTER\s+TABLE\s+(?P<name>[`\"\w.$-]+)", re.IGNORECASE),
        SymbolKind.Method,
        "Alter table",
    ),
    (
        re.compile(r"^\s*DROP\s+TABLE\s+(?P<name>[`\"\w.$-]+)", re.IGNORECASE),
        SymbolKind.Method,
        "Drop table",
    ),
    (
        re.compile(r"^\s*CREATE\s+CATALOG\s+(?P<name>[`\"\w.$-]+)", re.IGNORECASE),
        SymbolKind.Module,
        "Catalog",
    ),
    (
        re.compile(
            r"^\s*CREATE\s+ROUTINE\s+LOAD\s+(?P<name>[`\"\w.$-]+)", re.IGNORECASE
        ),
        SymbolKind.Object,
        "Routine load job",
    ),
    (
        re.compile(
            r"^\s*(?:STOP|PAUSE|RESUME)\s+ROUTINE\s+LOAD\s+(?P<name>[`\"\w.$-]+)",
            re.IGNORECASE,
        ),
        SymbolKind.Event,
        "Routine load command",
    ),
    (
        re.compile(r"^\s*SUBMIT\s+TASK\s+(?P<name>[`\"\w.$-]+)", re.IGNORECASE),
        SymbolKind.Event,
        "Task",
    ),
    (
        re.compile(r"^\s*USE\s+(?P<name>[`\"\w.$-]+)", re.IGNORECASE),
        SymbolKind.Namespace,
        "Database",
    ),
)

_STATEMENT_PREFIXES: tuple[tuple[str, SymbolKind, str], ...] = (
    ("EXPLAIN", SymbolKind.Operator, "Explain statement"),
    ("SELECT", SymbolKind.Function, "Query statement"),
    ("WITH", SymbolKind.Namespace, "CTE statement"),
    ("INSERT OVERWRITE", SymbolKind.Method, "Insert overwrite statement"),
    ("INSERT", SymbolKind.Method, "Insert statement"),
    ("UPDATE", SymbolKind.Method, "Update statement"),
    ("DELETE", SymbolKind.Method, "Delete statement"),
)


def _symbol_range(line_index: int, raw_line: str) -> Range:
    return Range(
        start=Position(line=line_index, character=0),
        end=Position(line=line_index, character=len(raw_line)),
    )


def _symbol_name(prefix: str, name: str | None, raw_line: str) -> str:
    if name:
        return f"{prefix} {name}"
    return raw_line.strip()[:80]


def statement_symbols(doc: TextDocumentLike) -> list[DocumentSymbol]:
    symbols: list[DocumentSymbol] = []
    for line_index, raw_line in enumerate(doc.lines):
        line = raw_line.strip()
        if not line:
            continue
        for pattern, kind, detail in _NAMED_STATEMENT_PATTERNS:
            match = pattern.match(raw_line)
            if match is not None:
                symbol_range = _symbol_range(line_index, raw_line)
                symbols.append(
                    DocumentSymbol(
                        name=_symbol_name(
                            detail, match.groupdict().get("name"), raw_line
                        ),
                        kind=kind,
                        range=symbol_range,
                        selection_range=symbol_range,
                        detail=detail,
                    )
                )
                break
        else:
            upper_line = line.upper()
            for keyword, kind, detail in _STATEMENT_PREFIXES:
                if upper_line.startswith(keyword):
                    symbol_range = _symbol_range(line_index, raw_line)
                    symbols.append(
                        DocumentSymbol(
                            name=line[:80],
                            kind=kind,
                            range=symbol_range,
                            selection_range=symbol_range,
                            detail=detail,
                        )
                    )
                    break
    if symbols:
        return symbols

    fallback_range = full_document_range(doc)
    return [
        DocumentSymbol(
            name="SQL Script",
            kind=SymbolKind.File,
            range=fallback_range,
            selection_range=fallback_range,
            detail="Entire document",
        )
    ]
