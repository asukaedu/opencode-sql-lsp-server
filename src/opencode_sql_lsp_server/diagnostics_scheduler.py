from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Coroutine, Any


@dataclass
class DocDiagnosticsState:
    version: int | None = None
    pending_timer: asyncio.TimerHandle | None = None
    pending_task: asyncio.Task[None] | None = None
    dialect: str | None = None
    dialect_root: Path | None = None
    dialect_config_mtime_ns: int | None = None


LintRunner = Callable[[str, int | None], Coroutine[Any, Any, None]]


class DiagnosticsScheduler:
    def __init__(self) -> None:
        self._doc_state: dict[str, DocDiagnosticsState] = {}

    def clear(self) -> None:
        for uri in list(self._doc_state):
            self.reset(uri, version=None)
        self._doc_state.clear()

    def document_state(self, uri: str) -> DocDiagnosticsState:
        return self._doc_state.setdefault(uri, DocDiagnosticsState())

    def reset(self, uri: str, version: int | None) -> DocDiagnosticsState:
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
        return state

    def schedule(
        self,
        uri: str,
        version: int | None,
        *,
        debounce_s: float,
        run_lint: LintRunner,
    ) -> None:
        state = self.reset(uri, version)
        loop = asyncio.get_running_loop()

        def kickoff() -> None:
            state.pending_timer = None
            state.pending_task = asyncio.create_task(
                run_lint(uri, version),
            )

        if debounce_s <= 0:
            kickoff()
        else:
            state.pending_timer = loop.call_later(debounce_s, kickoff)
