from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    DocumentFormattingParams,
    InitializeParams,
    INITIALIZE,
    PublishDiagnosticsParams,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_FORMATTING,
    Position,
    Range,
    TextEdit,
)
from pygls.lsp.server import LanguageServer
from pygls.uris import to_fs_path

from pygls.exceptions import PyglsError
from .config import SqlLspConfig
from .sqlfluff_adapter import format_sql, lint_issues


_DID_CHANGE_DEBOUNCE_S = 0.25
_MAX_LINT_LINES = 5_000
_MAX_LINT_BYTES = 200_000


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


class OpenCodeSqlLanguageServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("opencode-sql-lsp", "0.1.0", max_workers=2)
        self._workspace_roots: list[Path] = []
        self._config_cache: dict[Path, _ConfigCacheEntry] = {}
        self._doc_state: dict[str, _DocState] = {}

    def set_workspace_root(self, root: str | None) -> None:
        self.set_workspace_roots([root] if root else [])

    def set_workspace_roots(self, roots: list[str]) -> None:
        self._workspace_roots = [Path(r).resolve() for r in roots if r]
        self._config_cache.clear()

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
                p.relative_to(r)
            except Exception:
                continue
            l = len(str(r))
            if l > best_len:
                best = r
                best_len = l
        return best

    def _config_for_root(self, root: Path) -> SqlLspConfig:
        cfg_path = root / ".opencode" / "sql-lsp.json"
        try:
            st = cfg_path.stat()
            mtime_ns: int | None = st.st_mtime_ns
        except FileNotFoundError:
            mtime_ns = None
        except Exception as e:
            self.report_server_error(e, PyglsError)
            return SqlLspConfig.default()

        cached = self._config_cache.get(root)
        if cached and cached.mtime_ns == mtime_ns:
            return cached.config

        if mtime_ns is None:
            cfg = SqlLspConfig.default()
            self._config_cache[root] = _ConfigCacheEntry(mtime_ns=None, config=cfg)
            return cfg

        try:
            cfg = SqlLspConfig.load(root)
        except Exception as e:
            if cached and cached.config:
                if cached.last_error_mtime_ns != mtime_ns:
                    cached.last_error_mtime_ns = mtime_ns
                    self.report_server_error(e, PyglsError)
                return cached.config
            self.report_server_error(e, PyglsError)
            cfg = SqlLspConfig.default()

        self._config_cache[root] = _ConfigCacheEntry(mtime_ns=mtime_ns, config=cfg)
        return cfg

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


server = OpenCodeSqlLanguageServer()


@server.feature(INITIALIZE)
def initialize(ls: OpenCodeSqlLanguageServer, params: InitializeParams):
    roots: list[str] = []
    try:
        wf = getattr(params, "workspace_folders", None)
        if isinstance(wf, list):
            for f in wf:
                uri = getattr(f, "uri", None)
                if isinstance(uri, str) and uri:
                    try:
                        fs_path = to_fs_path(uri)
                        if fs_path:
                            roots.append(fs_path)
                    except Exception:
                        continue
    except Exception:
        pass

    root_uri = getattr(params, "root_uri", None)
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


def _is_large_document(source: str) -> bool:
    if len(source.encode("utf-8", errors="ignore")) > _MAX_LINT_BYTES:
        return True
    return source.count("\n") > _MAX_LINT_LINES


def _publish_skipped_diagnostics(ls: OpenCodeSqlLanguageServer, uri: str) -> None:
    ls.text_document_publish_diagnostics(
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


def _safe_position(doc, line_1: int, character: int) -> Position:
    if not getattr(doc, "lines", None):
        return Position(line=0, character=0)
    max_line = max(0, len(doc.lines) - 1)
    line_idx = min(max(0, line_1 - 1), max_line)
    line_text = doc.lines[line_idx] if doc.lines else ""
    char_idx = min(max(0, character), len(line_text))
    return Position(line=line_idx, character=char_idx)


def _issue_range(doc, issue) -> Range:
    start = _safe_position(doc, issue.line, issue.character)
    end_char = start.character + 1
    if getattr(doc, "lines", None):
        line_text = doc.lines[start.line]
        end_char = min(end_char, len(line_text))
    end = Position(line=start.line, character=end_char)
    return Range(start=start, end=end)


async def _run_lint_and_publish(
    ls: OpenCodeSqlLanguageServer,
    uri: str,
    expected_version: int | None,
) -> None:
    state = ls._doc_state.setdefault(uri, _DocState())
    try:
        doc = ls.workspace.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return

    source = doc.source
    dialect = ls.dialect_for_document(uri)
    loop = asyncio.get_running_loop()

    try:
        issues = await loop.run_in_executor(
            ls.thread_pool, lambda: lint_issues(source, dialect=dialect)
        )
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        issues = []

    if expected_version is not None and state.version != expected_version:
        return

    diagnostics: list[Diagnostic] = []
    for issue in issues:
        diagnostics.append(
            Diagnostic(
                range=_issue_range(doc, issue),
                message=issue.message,
                severity=DiagnosticSeverity.Error,
                source="sqlfluff",
            )
        )

    ls.text_document_publish_diagnostics(
        PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
    )


def _schedule_diagnostics(
    ls: OpenCodeSqlLanguageServer,
    uri: str,
    version: int | None,
    *,
    debounce_s: float,
) -> None:
    state = ls._doc_state.setdefault(uri, _DocState())
    state.version = version

    if state.pending_timer is not None:
        state.pending_timer.cancel()
        state.pending_timer = None

    if state.pending_task is not None and not state.pending_task.done():
        pass

    loop = asyncio.get_running_loop()

    def kickoff() -> None:
        state.pending_timer = None
        state.pending_task = asyncio.create_task(
            _run_lint_and_publish(ls, uri, expected_version=version)
        )

    if debounce_s <= 0:
        kickoff()
    else:
        state.pending_timer = loop.call_later(debounce_s, kickoff)


@server.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: OpenCodeSqlLanguageServer, params):
    uri = params.text_document.uri
    version = getattr(params.text_document, "version", None)
    try:
        doc = ls.workspace.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return
    else:
        if _is_large_document(doc.source):
            _publish_skipped_diagnostics(ls, uri)
            return
    _schedule_diagnostics(ls, uri, version, debounce_s=0.0)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: OpenCodeSqlLanguageServer, params):
    uri = params.text_document.uri
    version = getattr(params.text_document, "version", None)
    try:
        doc = ls.workspace.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return
    else:
        if _is_large_document(doc.source):
            _publish_skipped_diagnostics(ls, uri)
            return
    _schedule_diagnostics(ls, uri, version, debounce_s=_DID_CHANGE_DEBOUNCE_S)


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: OpenCodeSqlLanguageServer, params):
    uri = params.text_document.uri
    version = getattr(params.text_document, "version", None)
    try:
        doc = ls.workspace.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return
    else:
        if _is_large_document(doc.source):
            _publish_skipped_diagnostics(ls, uri)
            return
    _schedule_diagnostics(ls, uri, version, debounce_s=0.0)


@server.feature(TEXT_DOCUMENT_FORMATTING)
def formatting(ls: OpenCodeSqlLanguageServer, params: DocumentFormattingParams):
    uri = params.text_document.uri
    try:
        doc = ls.workspace.get_text_document(uri)
    except Exception as e:
        ls.report_server_error(e, PyglsError)
        return []

    dialect = ls.dialect_for_document(uri)
    try:
        formatted = format_sql(doc.source, dialect=dialect)
    except Exception:
        return []
    last_line = max(0, len(doc.lines) - 1)
    last_char = len(doc.lines[last_line]) if doc.lines else 0
    edit_range = Range(
        start=Position(line=0, character=0),
        end=Position(line=last_line, character=last_char),
    )
    return [TextEdit(range=edit_range, new_text=formatted)]
