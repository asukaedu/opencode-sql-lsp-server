from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

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
    SymbolKind,
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
from .sqlfluff_adapter import SqlIssue, format_sql, lint_issues


_DID_CHANGE_DEBOUNCE_S = 0.25

_KEYWORD_DETAILS: Final[dict[str, str]] = {
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

_KEYWORD_COMPLETIONS: Final[list[CompletionItem]] = [
    CompletionItem(
        label=keyword,
        kind=CompletionItemKind.Keyword,
        detail="SQL keyword",
        documentation=description,
        insert_text=keyword,
    )
    for keyword, description in _KEYWORD_DETAILS.items()
]


class _TextDocumentLike(Protocol):
    source: str
    lines: Sequence[str]


@dataclass
class _ConfigCacheEntry:
    mtime_ns: int | None
    config: SqlLspConfig
    last_error_mtime_ns: int | None = None


@dataclass
class _DocState:
    version: int | None = None
    pending_timer: asyncio.TimerHandle | None = None
    pending_task: asyncio.Task[None] | None = None
    dialect: str | None = None
    dialect_root: Path | None = None
    dialect_config_mtime_ns: int | None = None


class OpenCodeSqlLanguageServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("opencode-sql-lsp", __version__, max_workers=2)
        self._workspace_roots: list[Path] = []
        self._config_cache: dict[Path, _ConfigCacheEntry] = {}
        self._doc_state: dict[str, _DocState] = {}

    def document_state(self, uri: str) -> _DocState:
        return self._doc_state.setdefault(uri, _DocState())

    def get_text_document(self, uri: str) -> _TextDocumentLike:
        document = self.workspace.get_text_document(uri)
        return cast(_TextDocumentLike, cast(object, document))

    def set_workspace_root(self, root: str | None) -> None:
        self.set_workspace_roots([root] if root else [])

    def set_workspace_roots(self, roots: list[str]) -> None:
        self._workspace_roots = [Path(r).resolve() for r in roots if r]
        self._config_cache.clear()
        self._doc_state.clear()

    def _best_root_for_uri(self, doc_uri: str) -> Path | None:
        if not self._workspace_roots:
            return None
        try:
            fs_path = to_fs_path(doc_uri)
            if not fs_path:
                return None
            p = Path(fs_path).resolve()
        except Exception:
            return None

        best: Path | None = None
        best_len = -1
        for r in self._workspace_roots:
            try:
                _ = p.relative_to(r)
            except Exception:
                continue
            l = len(str(r))
            if l > best_len:
                best = r
                best_len = l
        return best

    def _config_for_root(self, root: Path) -> SqlLspConfig:
        return self._config_cache_entry_for_root(root).config

    def _config_cache_entry_for_root(self, root: Path) -> _ConfigCacheEntry:
        cfg_path = root / ".opencode" / "sql-lsp.json"
        try:
            st = cfg_path.stat()
            mtime_ns: int | None = st.st_mtime_ns
        except FileNotFoundError:
            mtime_ns = None
        except Exception as e:
            self.report_server_error(e, PyglsError)
            return _ConfigCacheEntry(mtime_ns=None, config=SqlLspConfig.default())

        cached = self._config_cache.get(root)
        if cached and cached.mtime_ns == mtime_ns:
            return cached

        if mtime_ns is None:
            cfg = SqlLspConfig.default()
            entry = _ConfigCacheEntry(mtime_ns=None, config=cfg)
            self._config_cache[root] = entry
            return entry

        try:
            cfg = SqlLspConfig.load(root)
        except Exception as e:
            if cached and cached.config:
                if cached.last_error_mtime_ns != mtime_ns:
                    cached.last_error_mtime_ns = mtime_ns
                    self.report_server_error(e, PyglsError)
                return cached
            self.report_server_error(e, PyglsError)
            cfg = SqlLspConfig.default()

        entry = _ConfigCacheEntry(mtime_ns=mtime_ns, config=cfg)
        self._config_cache[root] = entry
        return entry

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
        state = self._doc_state.setdefault(doc_uri, _DocState())
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
        state = self.document_state(uri)
        state.version = version
        state.dialect = None
        state.dialect_root = None
        state.dialect_config_mtime_ns = None
        if state.pending_timer is not None:
            state.pending_timer.cancel()
            state.pending_timer = None
        if state.pending_task is not None and not state.pending_task.done():
            _ = state.pending_task.cancel()
        state.pending_task = None
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
        loop = asyncio.get_running_loop()

        try:
            issues = await loop.run_in_executor(
                self.thread_pool, lambda: lint_issues(source, dialect=dialect)
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
                    range=_issue_range(doc, issue),
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
        state = self.document_state(uri)
        state.version = version
        state.dialect = None
        state.dialect_root = None
        state.dialect_config_mtime_ns = None

        if state.pending_timer is not None:
            state.pending_timer.cancel()
            state.pending_timer = None

        if state.pending_task is not None and not state.pending_task.done():
            _ = state.pending_task.cancel()
        state.pending_task = None

        loop = asyncio.get_running_loop()

        def kickoff() -> None:
            state.pending_timer = None
            state.pending_task = asyncio.create_task(
                self.run_lint_and_publish(uri, expected_version=version)
            )

        if debounce_s <= 0:
            kickoff()
        else:
            state.pending_timer = loop.call_later(debounce_s, kickoff)


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
    ls.set_workspace_roots(deduped)


def _safe_position(doc: _TextDocumentLike, line_1: int, character: int) -> Position:
    if not doc.lines:
        return Position(line=0, character=0)
    max_line = max(0, len(doc.lines) - 1)
    line_idx = min(max(0, line_1 - 1), max_line)
    line_text = doc.lines[line_idx]
    char_idx = min(max(0, character), len(line_text))
    return Position(line=line_idx, character=char_idx)


def _issue_range(doc: _TextDocumentLike, issue: SqlIssue) -> Range:
    start = _safe_position(doc, issue.line, issue.character)
    end_char = start.character + 1
    if doc.lines:
        line_text = doc.lines[start.line]
        end_char = min(end_char, len(line_text))
    end = Position(line=start.line, character=end_char)
    return Range(start=start, end=end)


def _full_document_range(doc: _TextDocumentLike) -> Range:
    last_line = max(0, len(doc.lines) - 1)
    last_char = len(doc.lines[last_line]) if doc.lines else 0
    return Range(
        start=Position(line=0, character=0),
        end=Position(line=last_line, character=last_char),
    )


def _word_range_at_position(doc: _TextDocumentLike, position: Position) -> Range | None:
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


def _word_at_position(
    doc: _TextDocumentLike, position: Position
) -> tuple[str, Range] | None:
    word_range = _word_range_at_position(doc, position)
    if word_range is None:
        return None
    line_text = doc.lines[position.line]
    token = line_text[word_range.start.character : word_range.end.character].strip()
    if not token:
        return None
    return token.upper(), word_range


def _statement_symbols(doc: _TextDocumentLike) -> list[DocumentSymbol]:
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

    fallback_range = _full_document_range(doc)
    return [
        DocumentSymbol(
            name="SQL Script",
            kind=SymbolKind.File,
            range=fallback_range,
            selection_range=fallback_range,
            detail="Entire document",
        )
    ]


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
    try:
        formatted = format_sql(doc.source, dialect=dialect)
    except Exception as e:
        ls.report_formatting_failure(e)
        return []
    edit_range = _full_document_range(doc)
    return [TextEdit(range=edit_range, new_text=formatted)]


@server.feature(TEXT_DOCUMENT_COMPLETION)
def completion(
    ls: OpenCodeSqlLanguageServer, params: CompletionParams
) -> list[CompletionItem]:
    dialect = ls.cached_dialect_for_document(params.text_document.uri)
    return [
        CompletionItem(
            label=item.label,
            kind=item.kind,
            detail=f"{item.detail} ({dialect})",
            documentation=item.documentation,
            insert_text=item.insert_text,
        )
        for item in _KEYWORD_COMPLETIONS
    ]


@server.feature(TEXT_DOCUMENT_HOVER)
def hover(ls: OpenCodeSqlLanguageServer, params: HoverParams) -> Hover | None:
    try:
        doc = ls.get_text_document(params.text_document.uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return None

    token_info = _word_at_position(doc, params.position)
    if token_info is None:
        return None

    token, token_range = token_info
    description = _KEYWORD_DETAILS.get(token)
    if description is None:
        return None

    dialect = ls.cached_dialect_for_document(params.text_document.uri)
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
    try:
        formatted = format_sql(doc.source, dialect=dialect)
    except Exception as e:
        ls.report_formatting_failure(e)
        return []

    return [
        CodeAction(
            title="Format with sqlfluff",
            kind=CodeActionKind.SourceFixAll,
            diagnostics=params.context.diagnostics,
            is_preferred=True,
            edit=WorkspaceEdit(
                changes={
                    uri: [TextEdit(range=_full_document_range(doc), new_text=formatted)]
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

    return _statement_symbols(doc)


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
        for symbol in _statement_symbols(doc):
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
