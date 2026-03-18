from __future__ import annotations

from lsprotocol.types import DocumentSymbol, Position, Range, SymbolKind

from .lsp_utils import TextDocumentLike, full_document_range


def statement_symbols(doc: TextDocumentLike) -> list[DocumentSymbol]:
    symbols: list[DocumentSymbol] = []
    for line_index, raw_line in enumerate(doc.lines):
        line = raw_line.strip()
        if not line:
            continue
        upper_line = line.upper()
        for keyword, kind in (
            ("SELECT", SymbolKind.Function),
            ("WITH", SymbolKind.Namespace),
            ("INSERT", SymbolKind.Method),
            ("UPDATE", SymbolKind.Method),
            ("DELETE", SymbolKind.Method),
            ("CREATE TABLE", SymbolKind.Class),
        ):
            if upper_line.startswith(keyword):
                symbol_range = Range(
                    start=Position(line=line_index, character=0),
                    end=Position(line=line_index, character=len(raw_line)),
                )
                symbols.append(
                    DocumentSymbol(
                        name=line[:80],
                        kind=kind,
                        range=symbol_range,
                        selection_range=symbol_range,
                        detail="SQL statement",
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
