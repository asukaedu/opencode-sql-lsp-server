from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import final
from typing import cast

from lsprotocol.types import CodeActionContext, CodeActionParams, Position, Range
from lsprotocol.types import (
    ClientCapabilities,
    CompletionParams,
    Diagnostic,
    DidChangeWorkspaceFoldersParams,
    DidCloseTextDocumentParams,
    DocumentFormattingParams,
    FormattingOptions,
    HoverParams,
    MarkupContent,
    PublishDiagnosticsParams,
    TextEdit,
    TextDocumentIdentifier,
    InitializeParams,
    WorkspaceFolder,
    WorkspaceFoldersChangeEvent,
)
import pytest
from pytest import MonkeyPatch

from opencode_sql_lsp_server import __version__
from opencode_sql_lsp_server import server as server_module


pytestmark = pytest.mark.server


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


def test_initialize_preserves_cli_workspace_override_when_client_roots_missing(
    tmp_path: Path,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = workspace / ".opencode"
    config_dir.mkdir()
    _ = (config_dir / "sql-lsp.json").write_text(
        '{"defaultDialect": "trino"}\n', encoding="utf-8"
    )
    target = workspace / "query.sql"
    _ = target.write_text("SELECT 1\n", encoding="utf-8")

    language_server.set_workspace_root(str(workspace))

    server_module.initialize(
        language_server,
        InitializeParams(capabilities=ClientCapabilities()),
    )

    assert language_server.cached_dialect_for_document(target.as_uri()) == "trino"


def test_did_close_clears_scheduler_state_and_publishes_empty_diagnostics() -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    uri = "file:///tmp/query.sql"
    state = language_server.document_state(uri)
    loop = asyncio.new_event_loop()
    published: list[PublishDiagnosticsParams] = []

    async def noop() -> None:
        await asyncio.sleep(60)

    timer = loop.call_later(60, lambda: None)
    pending_task = loop.create_task(noop())
    state.pending_timer = timer
    state.pending_task = pending_task
    state.dialect = "trino"
    state.dialect_root = Path("/tmp")
    state.dialect_config_mtime_ns = 99

    def publish(params: PublishDiagnosticsParams) -> None:
        published.append(params)

    language_server.text_document_publish_diagnostics = publish

    try:
        server_module.did_close(
            language_server,
            DidCloseTextDocumentParams(
                text_document=TextDocumentIdentifier(uri=uri),
            ),
        )

        assert timer.cancelled() is True
        assert uri not in language_server._diagnostics_scheduler._doc_state
        assert len(published) == 1
        assert published[0] == PublishDiagnosticsParams(uri=uri, diagnostics=[])
    finally:
        if not pending_task.done():
            _ = pending_task.cancel()
        _ = loop.run_until_complete(
            asyncio.gather(pending_task, return_exceptions=True)
        )
        loop.close()


def test_did_change_workspace_folders_refreshes_workspace_roots(
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

    language_server.set_workspace_roots([str(outer)])

    assert language_server.cached_dialect_for_document(target.as_uri()) == "starrocks"

    server_module.did_change_workspace_folders(
        language_server,
        DidChangeWorkspaceFoldersParams(
            event=WorkspaceFoldersChangeEvent(
                added=[WorkspaceFolder(uri=inner.as_uri(), name="nested")],
                removed=[],
            )
        ),
    )

    assert language_server.cached_dialect_for_document(target.as_uri()) == "trino"

    server_module.did_change_workspace_folders(
        language_server,
        DidChangeWorkspaceFoldersParams(
            event=WorkspaceFoldersChangeEvent(
                added=[],
                removed=[WorkspaceFolder(uri=inner.as_uri(), name="nested")],
            )
        ),
    )

    assert language_server.cached_dialect_for_document(target.as_uri()) == "starrocks"


def test_did_change_workspace_folders_ignores_invalid_folder_uris(
    tmp_path: Path,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    language_server.set_workspace_roots([str(workspace)])

    server_module.did_change_workspace_folders(
        language_server,
        DidChangeWorkspaceFoldersParams(
            event=WorkspaceFoldersChangeEvent(
                added=[WorkspaceFolder(uri="untitled:workspace", name="bad")],
                removed=[],
            )
        ),
    )

    assert language_server._workspace_roots == [workspace.resolve()]


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

    def raise_format_error(
        sql: str, *, dialect: str, file_path: str | None = None
    ) -> str:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        assert file_path == "/tmp/query.sql"
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


def test_formatting_success_returns_full_document_edit(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT  1\n")

    monkeypatch.setattr(language_server, "get_text_document", lambda uri: document)
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )
    monkeypatch.setattr(
        server_module,
        "format_sql",
        lambda sql, *, dialect, file_path=None: "SELECT 1\n",
    )

    result = server_module.formatting(
        language_server,
        DocumentFormattingParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
            options=FormattingOptions(tab_size=2, insert_spaces=True),
        ),
    )

    assert len(result) == 1
    assert result[0].new_text == "SELECT 1\n"
    assert result[0].range == Range(
        start=Position(line=0, character=0), end=Position(line=0, character=9)
    )


def test_formatting_returns_empty_edits_when_sql_is_unchanged(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT 1\n")

    monkeypatch.setattr(language_server, "get_text_document", lambda uri: document)
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )
    monkeypatch.setattr(
        server_module,
        "format_sql",
        lambda sql, *, dialect, file_path=None: "SELECT 1\n",
    )

    result = server_module.formatting(
        language_server,
        DocumentFormattingParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
            options=FormattingOptions(tab_size=2, insert_spaces=True),
        ),
    )

    assert result == []


def test_run_lint_and_publish_uses_configured_excluded_rules(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT 1\n")
    published: list[PublishDiagnosticsParams] = []

    def get_document(uri: str) -> FakeDocument:
        assert uri == "file:///tmp/query.sql"
        return document

    def get_dialect(uri: str) -> str:
        assert uri == "file:///tmp/query.sql"
        return "starrocks"

    def get_config(uri: str) -> object:
        assert uri == "file:///tmp/query.sql"
        return type("ConfigLike", (), {"excluded_rules": ("LT05", "ST06")})()

    def fake_lint(
        sql: str,
        *,
        dialect: str,
        excluded_rules: tuple[str, ...],
        file_path: str | None,
    ) -> list[object]:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        assert excluded_rules == ("LT05", "ST06")
        assert file_path == "/tmp/query.sql"
        return []

    monkeypatch.setattr(language_server, "get_text_document", get_document)
    monkeypatch.setattr(language_server, "cached_dialect_for_document", get_dialect)
    monkeypatch.setattr(language_server, "document_config", get_config)
    monkeypatch.setattr(
        language_server, "text_document_publish_diagnostics", published.append
    )
    monkeypatch.setattr(server_module, "lint_issues", fake_lint)

    asyncio.run(language_server.run_lint_and_publish("file:///tmp/query.sql", None))

    assert len(published) == 1
    assert published[0].diagnostics == []


def test_run_lint_and_publish_uses_none_for_non_file_uri(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT 1\n")
    published: list[PublishDiagnosticsParams] = []

    monkeypatch.setattr(language_server, "get_text_document", lambda uri: document)
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )
    monkeypatch.setattr(
        language_server,
        "document_config",
        lambda uri: type("ConfigLike", (), {"excluded_rules": ("LT05",)})(),
    )
    monkeypatch.setattr(
        language_server, "text_document_publish_diagnostics", published.append
    )

    def fake_lint(
        sql: str,
        *,
        dialect: str,
        excluded_rules: tuple[str, ...],
        file_path: str | None,
    ) -> list[object]:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        assert excluded_rules == ("LT05",)
        assert file_path is None
        return []

    monkeypatch.setattr(server_module, "lint_issues", fake_lint)

    asyncio.run(language_server.run_lint_and_publish("untitled:query.sql", None))

    assert len(published) == 1
    assert published[0].diagnostics == []


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

    def raise_action_error(
        sql: str, *, dialect: str, file_path: str | None = None
    ) -> str:
        assert sql == "SELECT 1\n"
        assert dialect == "starrocks"
        assert file_path == "/tmp/query.sql"
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


def test_code_action_returns_fix_all_edit_when_format_changes_document(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT  1\n")
    diagnostics = [
        Diagnostic(
            range=Range(
                start=Position(line=0, character=0),
                end=Position(line=0, character=1),
            ),
            message="fmt",
        )
    ]

    monkeypatch.setattr(language_server, "get_text_document", lambda uri: document)
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )
    monkeypatch.setattr(
        server_module,
        "format_sql",
        lambda sql, *, dialect, file_path=None: "SELECT 1\n",
    )

    result = server_module.code_action(
        language_server,
        CodeActionParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
            range=Range(
                start=Position(line=0, character=0), end=Position(line=0, character=6)
            ),
            context=CodeActionContext(diagnostics=diagnostics),
        ),
    )

    assert len(result) == 1
    assert result[0].title == "Format with sqlfluff"
    assert result[0].edit is not None
    assert result[0].edit.changes == {
        "file:///tmp/query.sql": [
            TextEdit(
                range=Range(
                    start=Position(line=0, character=0),
                    end=Position(line=0, character=9),
                ),
                new_text="SELECT 1\n",
            )
        ]
    }
    assert result[0].diagnostics == diagnostics


def test_code_action_returns_no_actions_when_sql_is_unchanged(
    monkeypatch: MonkeyPatch,
) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT 1\n")

    monkeypatch.setattr(language_server, "get_text_document", lambda uri: document)
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )
    monkeypatch.setattr(
        server_module,
        "format_sql",
        lambda sql, *, dialect, file_path=None: "SELECT 1\n",
    )

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


def test_completion_adds_starrocks_specific_keywords() -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )
    try:
        result = server_module.completion(
            language_server,
            CompletionParams(
                text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
                position=Position(line=0, character=0),
            ),
        )
    finally:
        monkeypatch.undo()

    labels = {item.label for item in result}
    assert "CREATE MATERIALIZED VIEW" in labels
    assert "CREATE ROUTINE LOAD" in labels
    assert "UNNEST" in labels


def test_completion_omits_starrocks_only_keywords_for_other_dialects() -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "trino"
    )
    try:
        result = server_module.completion(
            language_server,
            CompletionParams(
                text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
                position=Position(line=0, character=0),
            ),
        )
    finally:
        monkeypatch.undo()

    labels = {item.label for item in result}
    assert "SELECT" in labels
    assert "CREATE MATERIALIZED VIEW" not in labels
    assert "CREATE ROUTINE LOAD" not in labels


def test_hover_prefers_longest_matching_phrase(monkeypatch: MonkeyPatch) -> None:
    language_server = server_module.OpenCodeSqlLanguageServer()
    document = FakeDocument("SELECT * FROM t GROUP BY city\n")

    monkeypatch.setattr(language_server, "get_text_document", lambda uri: document)
    monkeypatch.setattr(
        language_server, "cached_dialect_for_document", lambda uri: "starrocks"
    )

    result = server_module.hover(
        language_server,
        HoverParams(
            text_document=TextDocumentIdentifier(uri="file:///tmp/query.sql"),
            position=Position(line=0, character=22),
        ),
    )

    assert result is not None
    assert result.range is not None
    assert result.range.start.character == 16
    assert result.range.end.character == 24
    assert "**GROUP BY** (starrocks)" in cast(MarkupContent, result.contents).value
