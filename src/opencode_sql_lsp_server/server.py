from __future__ import annotations

from pathlib import Path
from typing import Optional

from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    DocumentFormattingParams,
    InitializeParams,
    INITIALIZE,
    PublishDiagnosticsParams,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_FORMATTING,
    Position,
    Range,
    TextEdit,
)
from pygls.lsp.server import LanguageServer

from .config import SqlLspConfig
from .sqlfluff_adapter import format_sql, lint_issues


class OpenCodeSqlLanguageServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("opencode-sql-lsp", "0.1.0")
        self._workspace_root: Optional[Path] = None
        self._config: Optional[SqlLspConfig] = None

    def set_workspace_root(self, root: Optional[str]) -> None:
        self._workspace_root = Path(root).resolve() if root else None
        self._config = (
            SqlLspConfig.load(self._workspace_root) if self._workspace_root else None
        )

    def dialect_for_document(self, doc_uri: str) -> str:
        if not self._workspace_root or not self._config:
            return "starrocks"
        try:
            p = Path(self.uri_to_path(doc_uri)).resolve()
            rel = p.relative_to(self._workspace_root)
            return self._config.dialect_for_path(str(rel))
        except Exception:
            return self._config.default_dialect


server = OpenCodeSqlLanguageServer()


@server.feature(INITIALIZE)
def initialize(ls: OpenCodeSqlLanguageServer, params: InitializeParams):
    root_uri = getattr(params, "root_uri", None)
    if isinstance(root_uri, str) and root_uri:
        try:
            ls.set_workspace_root(ls.uri_to_path(root_uri))
        except Exception:
            pass


def _publish_diagnostics(ls: OpenCodeSqlLanguageServer, uri: str) -> None:
    doc = ls.workspace.get_text_document(uri)
    dialect = ls.dialect_for_document(uri)
    issues = lint_issues(doc.source, dialect=dialect)
    diagnostics: list[Diagnostic] = []
    for issue in issues:
        start = Position(line=issue.line - 1, character=issue.character)
        end = Position(line=issue.line - 1, character=issue.character + 1)
        diagnostics.append(
            Diagnostic(
                range=Range(start=start, end=end),
                message=issue.message,
                severity=DiagnosticSeverity.Error,
                source="sqlglot",
            )
        )
    ls.text_document_publish_diagnostics(
        PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
    )


@server.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: OpenCodeSqlLanguageServer, params):
    _publish_diagnostics(ls, params.text_document.uri)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: OpenCodeSqlLanguageServer, params):
    _publish_diagnostics(ls, params.text_document.uri)


@server.feature(TEXT_DOCUMENT_FORMATTING)
def formatting(ls: OpenCodeSqlLanguageServer, params: DocumentFormattingParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)
    dialect = ls.dialect_for_document(params.text_document.uri)
    try:
        formatted = format_sql(doc.source, dialect=dialect)
    except Exception:
        return []
    # Replace entire document
    last_line = max(0, len(doc.lines) - 1)
    last_char = len(doc.lines[last_line]) if doc.lines else 0
    edit_range = Range(
        start=Position(line=0, character=0),
        end=Position(line=last_line, character=last_char),
    )
    return [TextEdit(range=edit_range, new_text=formatted)]
