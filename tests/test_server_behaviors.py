from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import final

from lsprotocol.types import CodeActionContext, CodeActionParams, Position, Range
from lsprotocol.types import (
    DocumentFormattingParams,
    FormattingOptions,
    PublishDiagnosticsParams,
    TextDocumentIdentifier,
)
from pytest import MonkeyPatch

from opencode_sql_lsp_server import __version__
from opencode_sql_lsp_server import server as server_module


@final
@dataclass
class FakeDocument:
    source: str

    @property
    def lines(self) -> list[str]:
        return self.source.splitlines()


def test_language_server_uses_package_version() -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()

    assert language_server.version == __version__


def test_cached_dialect_for_document_prefers_most_specific_workspace(
    tmp_path: Path,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    outer = tmp_path / "workspace"
    inner = outer / "nested"
    inner.mkdir(parents=True)
    target = inner / "query.sql"
    _ = target.write_text("SELECT 1\n", encoding="utf-8")
    outer_config = outer / ".opencode"
    outer_config.mkdir()
    _ = (outer_config / "sql-lsp.json").write_text(
        '{"defaultDialect": "starrocks"}\n', encoding="utf-8"
    )
    inner_config = inner / ".opencode"
    inner_config.mkdir()
    _ = (inner_config / "sql-lsp.json").write_text(
        '{"defaultDialect": "trino"}\n', encoding="utf-8"
    )

    language_server.set_workspace_roots([str(outer), str(inner)])

    assert language_server.cached_dialect_for_document(target.as_uri()) == "trino"


def test_skip_diagnostics_for_large_document_resets_state_and_publishes_warning() -> (
    None
):
    language_server = server_module.OpenCodeSqlLanguageServer()
    uri = "file:///tmp/query.sql"
    state = language_server.document_state(uri)
    loop = asyncio.new_event_loop()

    published: list[PublishDiagnosticsParams] = []

    def publish(params: PublishDiagnosticsParams) -> None:
        published.append(params)

    async def noop() -> None:
        await asyncio.sleep(60)

    timer = loop.call_later(60, lambda: None)
    pending_task = loop.create_task(noop())
    state.pending_timer = timer
    state.pending_task = pending_task
    state.dialect = "trino"
    state.dialect_root = Path("/tmp")
    state.dialect_config_mtime_ns = 99
    language_server.text_document_publish_diagnostics = publish

    try:
        language_server.skip_diagnostics_for_large_document(uri, version=7)

        assert timer.cancelled() is True
        assert pending_task.cancelling() == 1
        assert state.pending_task is None
        assert state.version == 7
        assert state.dialect is None
        assert state.dialect_root is None
        assert state.dialect_config_mtime_ns is None
        assert len(published) == 1
        assert published[0].diagnostics[0].message == "Lint skipped (file too large)"
    finally:
        if not pending_task.done():
            _ = pending_task.cancel()
        _ = loop.run_until_complete(
            asyncio.gather(pending_task, return_exceptions=True)
        )
        loop.close()


def test_formatting_reports_failure_and_returns_empty_edits(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT 1\n")
    reported: list[str] = []

    def get_document(uri: str) -> FakeDocument:
        assert uri == "file:///tmp/query.sql"
        return document

    def get_dialect(uri: str) -> str:
        assert uri == "file:///tmp/query.sql"
        return "starrocks"

    def report_failure(error: Exception) -> None:
        reported.append(str(error))

    def raise_format_error(sql: str, *, dialect: str) -> str:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        raise RuntimeError("format boom")

    monkeypatch.setattr(language_server, "get_text_document", get_document)
    monkeypatch.setattr(language_server, "cached_dialect_for_document", get_dialect)
    monkeypatch.setattr(language_server, "report_formatting_failure", report_failure)
    monkeypatch.setattr(server_module, "format_sql", raise_format_error)

    result = server_module.formatting(
        language_server,
        DocumentFormattingParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
            options=FormattingOptions(tab_size=2, insert_spaces=True),
        ),
    )

    assert result == []
    assert reported == ["format boom"]


def test_code_action_reports_failure_and_returns_no_actions(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT 1\n")
    reported: list[str] = []

    def get_document(uri: str) -> FakeDocument:
        assert uri == "file:///tmp/query.sql"
        return document

    def get_dialect(uri: str) -> str:
        assert uri == "file:///tmp/query.sql"
        return "starrocks"

    def report_failure(error: Exception) -> None:
        reported.append(str(error))

    def raise_action_error(sql: str, *, dialect: str) -> str:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        raise RuntimeError("action boom")

    monkeypatch.setattr(language_server, "get_text_document", get_document)
    monkeypatch.setattr(language_server, "cached_dialect_for_document", get_dialect)
    monkeypatch.setattr(language_server, "report_formatting_failure", report_failure)
    monkeypatch.setattr(server_module, "format_sql", raise_action_error)

    result = server_module.code_action(
        language_server,
        CodeActionParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
            range=Range(
                start=Position(line=0, character=0), end=Position(line=0, character=6)
            ),
            context=CodeActionContext(diagnostics=[]),
        ),
    )

    assert result == []
    assert reported == ["action boom"]
