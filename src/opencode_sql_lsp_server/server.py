from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Final, cast

from lsprotocol.types import (
    CodeAction,
    CodeActionKind,
    CodeActionParams,
    CompletionItem,
    CompletionItemKind,
    CompletionParams,
    Diagnostic,
    DiagnosticSeverity,
    DidChangeTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentSymbol,
    DocumentSymbolParams,
    DocumentFormattingParams,
    Hover,
    HoverParams,
    InitializeParams,
    INITIALIZE,
    Location,
    MarkupContent,
    MarkupKind,
    PublishDiagnosticsParams,
    SymbolInformation,
    TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_COMPLETION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    TEXT_DOCUMENT_FORMATTING,
    TEXT_DOCUMENT_HOVER,
    Position,
    Range,
    TextEdit,
    WORKSPACE_SYMBOL,
    WorkspaceEdit,
    WorkspaceSymbolParams,
)
from pygls.lsp.server import LanguageServer
from pygls.uris import to_fs_path

from pygls.exceptions import PyglsError
from . import __version__
from .config import SqlLspConfig
from .diagnostics_scheduler import DiagnosticsScheduler, DocDiagnosticsState
from .lsp_utils import (
    TextDocumentLike,
    full_document_range,
    issue_range,
    word_at_position,
)
from .sqlfluff_adapter import format_sql, lint_issues
from .symbol_provider import statement_symbols
from .workspace_config import (
    ConfigCacheEntry,
    best_root_for_uri,
    config_cache_entry_for_root,
    config_for_root,
)


_DID_CHANGE_DEBOUNCE_S = 0.25

_COMMON_KEYWORD_DETAILS: Final[dict[str, str]] = {
    "SELECT": "Query rows from a table or subquery.",
    "FROM": "Choose the source relation for the query.",
    "WHERE": "Filter rows before projection or aggregation.",
    "JOIN": "Combine rows from multiple relations.",
    "LEFT JOIN": "Keep all left-side rows while joining matching right-side rows.",
    "GROUP BY": "Aggregate rows by one or more expressions.",
    "ORDER BY": "Sort the result set.",
    "INSERT": "Add rows to a table.",
    "UPDATE": "Modify existing rows in a table.",
    "DELETE": "Remove rows from a table.",
    "CREATE TABLE": "Define a new table schema.",
    "WITH": "Start a common table expression (CTE).",
}

_STARROCKS_KEYWORD_DETAILS: Final[dict[str, str]] = {
    "INSERT OVERWRITE": "Replace data in the target table or partition with query output.",
    "CREATE VIEW": "Define a reusable logical query.",
    "CREATE MATERIALIZED VIEW": "Persist a query result for StarRocks acceleration.",
    "REFRESH MATERIALIZED VIEW": "Refresh a StarRocks materialized view manually.",
    "ALTER TABLE": "Change an existing table schema or properties.",
    "DROP TABLE": "Remove a table definition and its data.",
    "PARTITION BY": "Define StarRocks table partitioning columns or expressions.",
    "DISTRIBUTED BY": "Define StarRocks bucket distribution for table storage.",
    "PROPERTIES": "Attach StarRocks engine or load options as key/value pairs.",
    "CREATE ROUTINE LOAD": "Create a continuous ingestion job from an external stream.",
    "STOP ROUTINE LOAD": "Stop a StarRocks routine load job.",
    "PAUSE ROUTINE LOAD": "Pause a StarRocks routine load job.",
    "RESUME ROUTINE LOAD": "Resume a paused StarRocks routine load job.",
    "SUBMIT TASK": "Submit an asynchronous StarRocks task such as manual refresh work.",
    "CREATE CATALOG": "Register an external catalog for federated querying.",
    "UNNEST": "Expand an array or collection into rows in the FROM clause.",
    "JSON_EACH": "Expand a JSON object into key/value rows in the FROM clause.",
}


def _keyword_details_for_dialect(dialect: str) -> dict[str, str]:
    if dialect.casefold() == "starrocks":
        return {**_COMMON_KEYWORD_DETAILS, **_STARROCKS_KEYWORD_DETAILS}
    return dict(_COMMON_KEYWORD_DETAILS)


def _completion_items_for_dialect(dialect: str) -> list[CompletionItem]:
    details = _keyword_details_for_dialect(dialect)
    return [
        CompletionItem(
            label=keyword,
            kind=CompletionItemKind.Keyword,
            detail=f"SQL keyword ({dialect})",
            documentation=description,
            insert_text=keyword,
        )
        for keyword, description in details.items()
    ]


def _is_keyword_boundary(char: str) -> bool:
    return not (char.isalnum() or char == "_")


def _keyword_hover_match(
    doc: TextDocumentLike, position: Position, dialect: str
) -> tuple[str, str, Range] | None:
    if position.line < 0 or position.line >= len(doc.lines):
        return None
    line_text = doc.lines[position.line]
    if not line_text:
        return None
    line_upper = line_text.upper()
    for keyword, description in sorted(
        _keyword_details_for_dialect(dialect).items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = re.escape(keyword)
        for match in re.finditer(pattern, line_upper):
            start = match.start()
            end = match.end()
            if start > 0 and not _is_keyword_boundary(line_upper[start - 1]):
                continue
            if end < len(line_upper) and not _is_keyword_boundary(line_upper[end]):
                continue
            if not (start <= position.character < end):
                continue
            return (
                keyword,
                description,
                Range(
                    start=Position(line=position.line, character=start),
                    end=Position(line=position.line, character=end),
                ),
            )
    return None


class OpenCodeSqlLanguageServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("opencode-sql-lsp", __version__, max_workers=2)
        self._workspace_roots: list[Path] = []
        self._config_cache: dict[Path, ConfigCacheEntry] = {}
        self._diagnostics_scheduler = DiagnosticsScheduler()

    def document_state(self, uri: str) -> DocDiagnosticsState:
        return self._diagnostics_scheduler.document_state(uri)

    def get_text_document(self, uri: str) -> TextDocumentLike:
        document = self.workspace.get_text_document(uri)
        return cast(TextDocumentLike, cast(object, document))

    def set_workspace_root(self, root: str | None) -> None:
        self.set_workspace_roots([root] if root else [])

    def set_workspace_roots(self, roots: list[str]) -> None:
        self._workspace_roots = [Path(r).resolve() for r in roots if r]
        self._config_cache.clear()
        self._diagnostics_scheduler.clear()

    def _best_root_for_uri(self, doc_uri: str) -> Path | None:
        return best_root_for_uri(self._workspace_roots, doc_uri)

    def _config_for_root(self, root: Path) -> SqlLspConfig:
        return config_for_root(
            root,
            config_cache=self._config_cache,
            report_server_error=self.report_server_error,
        )

    def _config_cache_entry_for_root(self, root: Path) -> ConfigCacheEntry:
        return config_cache_entry_for_root(
            root,
            config_cache=self._config_cache,
            report_server_error=self.report_server_error,
        )

    def dialect_for_document(self, doc_uri: str) -> str:
        root = self._best_root_for_uri(doc_uri)
        if not root:
            return SqlLspConfig.default().default_dialect
        cfg = self._config_for_root(root)
        try:
            fs_path = to_fs_path(doc_uri)
            if not fs_path:
                return cfg.default_dialect
            p = Path(fs_path).resolve()
            rel = p.relative_to(root)
            return cfg.dialect_for_path(str(rel))
        except Exception:
            return cfg.default_dialect

    def cached_dialect_for_document(self, doc_uri: str) -> str:
        state = self.document_state(doc_uri)
        root = self._best_root_for_uri(doc_uri)
        if not root:
            state.dialect = SqlLspConfig.default().default_dialect
            state.dialect_root = None
            state.dialect_config_mtime_ns = None
            return state.dialect

        entry = self._config_cache_entry_for_root(root)
        if (
            state.dialect is not None
            and state.dialect_root == root
            and state.dialect_config_mtime_ns == entry.mtime_ns
        ):
            return state.dialect

        try:
            fs_path = to_fs_path(doc_uri)
            if not fs_path:
                dialect = entry.config.default_dialect
            else:
                p = Path(fs_path).resolve()
                rel = p.relative_to(root)
                dialect = entry.config.dialect_for_path(str(rel))
        except Exception:
            dialect = entry.config.default_dialect

        state.dialect = dialect
        state.dialect_root = root
        state.dialect_config_mtime_ns = entry.mtime_ns
        return dialect

    def publish_skipped_diagnostics(self, uri: str) -> None:
        self.text_document_publish_diagnostics(
            PublishDiagnosticsParams(
                uri=uri,
                diagnostics=[
                    Diagnostic(
                        range=Range(
                            start=Position(line=0, character=0),
                            end=Position(line=0, character=0),
                        ),
                        message="Lint skipped (file too large)",
                        severity=DiagnosticSeverity.Warning,
                        source="opencode-sql-lsp",
                    )
                ],
            )
        )

    def report_formatting_failure(self, error: Exception) -> None:
        self.report_server_error(error, PyglsError)

    def skip_diagnostics_for_large_document(
        self, uri: str, version: int | None
    ) -> None:
        _ = self._diagnostics_scheduler.reset(uri, version)
        self.publish_skipped_diagnostics(uri)

    def document_config(self, uri: str) -> SqlLspConfig:
        root = self._best_root_for_uri(uri)
        if not root:
            return SqlLspConfig.default()
        return self._config_for_root(root)

    def is_large_document(self, uri: str, source: str) -> bool:
        config = self.document_config(uri)
        if len(source.encode("utf-8", errors="ignore")) > config.max_lint_bytes:
            return True
        return source.count("\n") > config.max_lint_lines

    def file_path_for_uri(self, uri: str) -> str | None:
        try:
            return to_fs_path(uri)
        except Exception:
            return None

    async def run_lint_and_publish(
        self,
        uri: str,
        expected_version: int | None,
    ) -> None:
        state = self.document_state(uri)
        try:
            doc = self.get_text_document(uri)
        except Exception as e:
            self.report_server_error(e, PyglsError)
            return

        source = doc.source
        dialect = self.cached_dialect_for_document(uri)
        config = self.document_config(uri)
        loop = asyncio.get_running_loop()

        file_path = self.file_path_for_uri(uri)

        try:
            issues = await loop.run_in_executor(
                self.thread_pool,
                lambda: lint_issues(
                    source,
                    dialect=dialect,
                    excluded_rules=config.excluded_rules,
                    file_path=file_path,
                ),
            )
        except asyncio.CancelledError:
            return
        except Exception as e:
            self.report_server_error(e, PyglsError)
            issues = []

        if expected_version is not None and state.version != expected_version:
            return

        diagnostics: list[Diagnostic] = []
        for issue in issues:
            message = issue.message
            if issue.code:
                message = f"[{issue.code}] {message}"
            diagnostics.append(
                Diagnostic(
                    range=issue_range(doc, issue),
                    message=message,
                    severity=DiagnosticSeverity.Error,
                    source="sqlfluff",
                )
            )

        self.text_document_publish_diagnostics(
            PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
        )

    def schedule_diagnostics(
        self,
        uri: str,
        version: int | None,
        *,
        debounce_s: float,
    ) -> None:
        self._diagnostics_scheduler.schedule(
            uri,
            version,
            debounce_s=debounce_s,
            run_lint=self.run_lint_and_publish,
        )


server = OpenCodeSqlLanguageServer()


@server.feature(INITIALIZE)
def initialize(ls: OpenCodeSqlLanguageServer, params: InitializeParams) -> None:
    roots: list[str] = []
    for folder in params.workspace_folders or []:
        try:
            fs_path = to_fs_path(folder.uri)
            if fs_path:
                roots.append(fs_path)
        except Exception:
            continue

    root_uri = params.root_uri
    if isinstance(root_uri, str) and root_uri:
        try:
            fs_path = to_fs_path(root_uri)
            if fs_path:
                roots.append(fs_path)
        except Exception:
            pass

    deduped: list[str] = []
    seen: set[str] = set()
    for r in roots:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    if deduped:
        ls.set_workspace_roots(deduped)


@server.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: OpenCodeSqlLanguageServer, params: DidOpenTextDocumentParams) -> None:
    uri = params.text_document.uri
    version = params.text_document.version
    try:
        doc = ls.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return
    else:
        if ls.is_large_document(uri, doc.source):
            ls.skip_diagnostics_for_large_document(uri, version)
            return
    ls.schedule_diagnostics(uri, version, debounce_s=0.0)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(
    ls: OpenCodeSqlLanguageServer, params: DidChangeTextDocumentParams
) -> None:
    uri = params.text_document.uri
    version = params.text_document.version
    try:
        doc = ls.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return
    else:
        if ls.is_large_document(uri, doc.source):
            ls.skip_diagnostics_for_large_document(uri, version)
            return
    ls.schedule_diagnostics(uri, version, debounce_s=_DID_CHANGE_DEBOUNCE_S)


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: OpenCodeSqlLanguageServer, params: DidSaveTextDocumentParams) -> None:
    uri = params.text_document.uri
    version = None
    try:
        doc = ls.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return
    else:
        if ls.is_large_document(uri, doc.source):
            ls.skip_diagnostics_for_large_document(uri, version)
            return
    ls.schedule_diagnostics(uri, version, debounce_s=0.0)


@server.feature(TEXT_DOCUMENT_FORMATTING)
def formatting(
    ls: OpenCodeSqlLanguageServer, params: DocumentFormattingParams
) -> list[TextEdit]:
    uri = params.text_document.uri
    try:
        doc = ls.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return []

    dialect = ls.cached_dialect_for_document(uri)
    file_path = ls.file_path_for_uri(uri)
    try:
        formatted = format_sql(doc.source, dialect=dialect, file_path=file_path)
    except Exception as e:
        ls.report_formatting_failure(e)
        return []
    if formatted == doc.source:
        return []
    edit_range = full_document_range(doc)
    return [TextEdit(range=edit_range, new_text=formatted)]


@server.feature(TEXT_DOCUMENT_COMPLETION)
def completion(
    ls: OpenCodeSqlLanguageServer, params: CompletionParams
) -> list[CompletionItem]:
    dialect = ls.cached_dialect_for_document(params.text_document.uri)
    return _completion_items_for_dialect(dialect)


@server.feature(TEXT_DOCUMENT_HOVER)
def hover(ls: OpenCodeSqlLanguageServer, params: HoverParams) -> Hover | None:
    try:
        doc = ls.get_text_document(params.text_document.uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return None

    dialect = ls.cached_dialect_for_document(params.text_document.uri)
    phrase_match = _keyword_hover_match(doc, params.position, dialect)
    if phrase_match is not None:
        token, description, token_range = phrase_match
    else:
        token_info = word_at_position(doc, params.position)
        if token_info is None:
            return None
        token, token_range = token_info
        description = _keyword_details_for_dialect(dialect).get(token)
        if description is None:
            return None

    return Hover(
        contents=MarkupContent(
            kind=MarkupKind.Markdown,
            value=f"**{token}** ({dialect})\n\n{description}",
        ),
        range=token_range,
    )


@server.feature(TEXT_DOCUMENT_CODE_ACTION)
def code_action(
    ls: OpenCodeSqlLanguageServer, params: CodeActionParams
) -> list[CodeAction]:
    uri = params.text_document.uri
    try:
        doc = ls.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return []

    dialect = ls.cached_dialect_for_document(uri)
    file_path = ls.file_path_for_uri(uri)
    try:
        formatted = format_sql(doc.source, dialect=dialect, file_path=file_path)
    except Exception as e:
        ls.report_formatting_failure(e)
        return []
    if formatted == doc.source:
        return []

    return [
        CodeAction(
            title="Format with sqlfluff",
            kind=CodeActionKind.SourceFixAll,
            diagnostics=params.context.diagnostics,
            is_preferred=True,
            edit=WorkspaceEdit(
                changes={
                    uri: [TextEdit(range=full_document_range(doc), new_text=formatted)]
                }
            ),
        )
    ]


@server.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(
    ls: OpenCodeSqlLanguageServer, params: DocumentSymbolParams
) -> list[DocumentSymbol]:
    try:
        doc = ls.get_text_document(params.text_document.uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return []

    return statement_symbols(doc)


@server.feature(WORKSPACE_SYMBOL)
def workspace_symbol(
    ls: OpenCodeSqlLanguageServer, params: WorkspaceSymbolParams
) -> list[SymbolInformation]:
    query = params.query.upper().strip()
    matches: list[SymbolInformation] = []
    for uri in list(ls.workspace.text_documents.keys()):
        try:
            doc = ls.get_text_document(uri)
        except Exception:
            continue
        for symbol in statement_symbols(doc):
            if query and query not in symbol.name.upper():
                continue
            matches.append(
                SymbolInformation(
                    name=symbol.name,
                    kind=symbol.kind,
                    location=Location(uri=uri, range=symbol.selection_range),
                    container_name="SQL Script",
                )
            )
    return matches
