from __future__ import annotations

import asyncio

import pytest

from opencode_sql_lsp_server.diagnostics_scheduler import DiagnosticsScheduler


pytestmark = pytest.mark.server


def test_reset_cancels_pending_work_and_clears_dialect_cache() -> None:
    scheduler = DiagnosticsScheduler()
    uri = "file:///tmp/query.sql"
    state = scheduler.document_state(uri)
    loop = asyncio.new_event_loop()

    async def sleeper() -> None:
        await asyncio.sleep(60)

    timer = loop.call_later(60, lambda: None)
    pending_task = loop.create_task(sleeper())
    state.pending_timer = timer
    state.pending_task = pending_task
    state.dialect = "trino"
    state.dialect_config_mtime_ns = 123

    try:
        reset_state = scheduler.reset(uri, version=4)

        assert timer.cancelled() is True
        assert reset_state.pending_timer is None
        assert reset_state.pending_task is None
        assert reset_state.version == 4
        assert reset_state.dialect is None
        assert reset_state.dialect_root is None
        assert reset_state.dialect_config_mtime_ns is None
    finally:
        if not pending_task.done():
            _ = pending_task.cancel()
        _ = loop.run_until_complete(
            asyncio.gather(pending_task, return_exceptions=True)
        )
        loop.close()


def test_schedule_replaces_pending_timer_and_runs_latest_version() -> None:
    async def run_test() -> None:
        scheduler = DiagnosticsScheduler()
        uri = "file:///tmp/query.sql"
        seen: list[tuple[str, int | None]] = []

        async def run_lint(target_uri: str, version: int | None) -> None:
            seen.append((target_uri, version))

        scheduler.schedule(uri, 1, debounce_s=0.05, run_lint=run_lint)
        first_timer = scheduler.document_state(uri).pending_timer
        assert first_timer is not None

        scheduler.schedule(uri, 2, debounce_s=0.01, run_lint=run_lint)

        assert first_timer.cancelled() is True
        await asyncio.sleep(0.03)
        pending_task = scheduler.document_state(uri).pending_task
        if pending_task is not None:
            await pending_task

        assert seen == [(uri, 2)]

    asyncio.run(run_test())


def test_schedule_with_zero_debounce_runs_immediately() -> None:
    async def run_test() -> None:
        scheduler = DiagnosticsScheduler()
        uri = "file:///tmp/query.sql"
        seen: list[int | None] = []

        async def run_lint(_uri: str, version: int | None) -> None:
            seen.append(version)

        scheduler.schedule(uri, 9, debounce_s=0.0, run_lint=run_lint)
        pending_task = scheduler.document_state(uri).pending_task

        assert pending_task is not None
        await pending_task
        assert seen == [9]

    asyncio.run(run_test())


def test_drop_cancels_pending_work_and_removes_document_state() -> None:
    scheduler = DiagnosticsScheduler()
    uri = "file:///tmp/query.sql"
    state = scheduler.document_state(uri)
    loop = asyncio.new_event_loop()

    async def sleeper() -> None:
        await asyncio.sleep(60)

    timer = loop.call_later(60, lambda: None)
    pending_task = loop.create_task(sleeper())
    state.pending_timer = timer
    state.pending_task = pending_task

    try:
        scheduler.drop(uri)

        assert timer.cancelled() is True
        assert uri not in scheduler._doc_state
    finally:
        if not pending_task.done():
            _ = pending_task.cancel()
        _ = loop.run_until_complete(
            asyncio.gather(pending_task, return_exceptions=True)
        )
        loop.close()
