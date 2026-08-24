"""Safe session projection, recovery and fork helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from undomesh.core.context import ConversationContext
from undomesh.runtime.projection import project_messages
from undomesh.runtime.store import SQLiteEventStore


class ReplayMode(StrEnum):
    RECORDED_TOOLS = "recorded-tools"
    RECORDED_MODEL = "recorded-model"
    DRY_RUN = "dry-run"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    session_id: str
    mode: ReplayMode
    messages: list[dict[str, Any]]
    event_count: int
    side_effects_executed: bool = False


class ReplayEngine:
    """Reconstruct recorded state; replay never executes a tool by default."""

    def __init__(self, store: SQLiteEventStore) -> None:
        self.store = store

    def replay(self, session_id: str, mode: ReplayMode = ReplayMode.DRY_RUN) -> ReplayResult:
        events = self.store.list_events(session_id)
        return ReplayResult(
            session_id=session_id,
            mode=mode,
            messages=project_messages(events),
            event_count=len(events),
        )

    def restore_context(self, context: ConversationContext, session_id: str) -> None:
        context.session_id = session_id
        context.messages = self.replay(session_id).messages

    def fork(
        self,
        source_session_id: str,
        *,
        through_sequence: int | None = None,
        new_session_id: str | None = None,
    ) -> str:
        target = new_session_id or str(uuid4())
        return self.store.fork_session(
            source_session_id,
            target,
            through_sequence=through_sequence,
        )
