from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from lsprotocol.types import Position, SymbolKind

from opencode_sql_lsp_server.lsp_utils import (
    TextDocumentLike,
    full_document_range,
    word_at_position,
)
from opencode_sql_lsp_server.symbol_provider import statement_symbols


pytestmark = pytest.mark.lsp_helpers


@dataclass
class FakeDocument:
    source: str

    @property
    def lines(self) -> list[str]:
        return self.source.splitlines()


def test_word_at_position_returns_uppercase_token_and_range() -> None:
    document = cast(TextDocumentLike, FakeDocument("select col_name\n"))

    result = word_at_position(document, Position(line=0, character=3))

    assert result is not None
    assert result[0] == "SELECT"
    assert result[1].start.character == 0
    assert result[1].end.character == 6


def test_statement_symbols_falls_back_to_file_symbol_for_blank_document() -> None:
    document = cast(TextDocumentLike, FakeDocument("\n\n"))

    result = statement_symbols(document)

    assert len(result) == 1
    assert result[0].name == "SQL Script"
    assert result[0].kind == SymbolKind.File
    assert result[0].range == full_document_range(document)


def test_statement_symbols_extracts_named_starrocks_entities() -> None:
    document = cast(
        TextDocumentLike,
        FakeDocument(
            "CREATE MATERIALIZED VIEW mv_sales AS SELECT 1\n"
            "CREATE ROUTINE LOAD load_orders ON t PROPERTIES()\n"
            "CREATE CATALOG hive_catalog PROPERTIES()\n"
        ),
    )

    result = statement_symbols(document)

    assert [symbol.name for symbol in result] == [
        "Materialized view mv_sales",
        "Routine load job load_orders",
        "Catalog hive_catalog",
    ]
    assert [symbol.detail for symbol in result] == [
        "Materialized view",
        "Routine load job",
        "Catalog",
    ]
