"""Prompt checkpoints and append-only code/conversation rewind orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from undomesh.runtime.events import AgentEvent, EventType
from undomesh.runtime.projection import project_messages

if TYPE_CHECKING:
    from undomesh.core.context import ConversationContext
    from undomesh.execution.transactions import DurableChangeEngine
    from undomesh.runtime.store import EventStore


class RewindMode(StrEnum):
    CODE = "code"
    CONVERSATION = "conversation"
    BOTH = "both"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class PromptCheckpoint:
    checkpoint_id: str
    session_id: str
    sequence: int
    turn_id: str
    prompt: str
    transaction_ids: tuple[str, ...]
    code_health: str
    conflict_paths: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RewindReceipt:
    rewind_id: str
    mode: RewindMode
    checkpoint_id: str
    source_session_id: str
    active_session_id: str
    restore_id: str = ""
    transaction_ids: tuple[str, ...] = ()
    summary: str = ""


class CheckpointManager:
    """Compose immutable runtime history with compensating code restores."""

    def __init__(
        self,
        event_store: EventStore,
        change_engine: DurableChangeEngine,
        context: ConversationContext | None = None,
    ) -> None:
        self.event_store = event_store
        self.change_engine = change_engine
        self.context = context

    def list_checkpoints(self, session_id: str | None = None) -> tuple[PromptCheckpoint, ...]:
        active = session_id or self._active_session_id()
        events = self.event_store.list_events(active)
        checkpoints = []
        live_cache: dict[str, Any] = {}
        for event in events:
            if event.event_type != EventType.TURN_STARTED:
                continue
            transaction_ids = self._transactions_after(events, event.sequence)
            health = "available"
            conflicts: tuple[str, ...] = ()
            unavailable: tuple[str, ...] = ()
            if transaction_ids:
                try:
                    preview = self.change_engine.restore_coordinator.preview(
                        transaction_ids, live_cache=live_cache
                    )
                    health = preview.health
                    conflicts = preview.conflicts
                    unavailable = preview.unavailable
                except Exception as exc:
                    health = "unavailable"
                    unavailable = (f"{type(exc).__name__}: {exc}",)
            checkpoints.append(PromptCheckpoint(
                checkpoint_id=event.event_id,
                session_id=active,
                sequence=event.sequence,
                turn_id=str(event.turn_id or ""),
                prompt=str(event.payload.get("input", "")),
                transaction_ids=transaction_ids,
                code_health=health,
                conflict_paths=conflicts,
                unavailable=unavailable,
            ))
        return tuple(checkpoints)

    def rewind(
        self,
        checkpoint_id: str,
        mode: RewindMode | str = RewindMode.BOTH,
        *,
        session_id: str | None = None,
    ) -> RewindReceipt:
        selected_mode = RewindMode(mode)
        source_session = session_id or self._active_session_id()
        checkpoint = self._find_checkpoint(source_session, checkpoint_id)
        rewind_id = str(uuid4())
        self.event_store.append(
            session_id=source_session,
            event_type=EventType.REWIND_REQUESTED,
            payload={
                "rewind_id": rewind_id,
                "checkpoint_id": checkpoint_id,
                "mode": selected_mode.value,
                "transaction_ids": list(checkpoint.transaction_ids),
            },
        )
        restore_id = ""
        active_session = source_session
        summary = ""
        try:
            if selected_mode in {RewindMode.CODE, RewindMode.BOTH} and checkpoint.transaction_ids:
                preview = self.change_engine.preview_restore(checkpoint.transaction_ids)
                if preview.health != "available":
                    raise RuntimeError(
                        f"Checkpoint code is not safely restorable: {preview.health}; "
                        f"conflicts={preview.conflicts}; unavailable={preview.unavailable}"
                    )
                restore = self.change_engine.restore_transactions(checkpoint.transaction_ids)
                restore_id = restore.restore_id

            if selected_mode in {RewindMode.CONVERSATION, RewindMode.BOTH}:
                active_session = str(uuid4())[:8]
                self.event_store.fork_session(
                    source_session,
                    active_session,
                    through_sequence=checkpoint.sequence,
                )
                self.event_store.append(
                    session_id=active_session,
                    event_type=EventType.SESSION_REWOUND,
                    payload={
                        "rewind_id": rewind_id,
                        "source_session_id": source_session,
                        "checkpoint_id": checkpoint_id,
                        "restore_id": restore_id,
                    },
                )
                if self.context is not None:
                    self.context.session_id = active_session
                    self.context.messages = project_messages(
                        self.event_store.list_events(active_session)
                    )
            elif selected_mode == RewindMode.SUMMARY:
                summary = self._summarize_after(source_session, checkpoint.sequence)

            self.event_store.append(
                session_id=active_session,
                event_type=EventType.REWIND_COMPLETED,
                payload={
                    "rewind_id": rewind_id,
                    "checkpoint_id": checkpoint_id,
                    "mode": selected_mode.value,
                    "source_session_id": source_session,
                    "restore_id": restore_id,
                },
            )
            if active_session != source_session:
                self.event_store.append(
                    session_id=source_session,
                    event_type=EventType.REWIND_COMPLETED,
                    payload={
                        "rewind_id": rewind_id,
                        "checkpoint_id": checkpoint_id,
                        "mode": selected_mode.value,
                        "active_session_id": active_session,
                        "restore_id": restore_id,
                    },
                )
            return RewindReceipt(
                rewind_id=rewind_id,
                mode=selected_mode,
                checkpoint_id=checkpoint_id,
                source_session_id=source_session,
                active_session_id=active_session,
                restore_id=restore_id,
                transaction_ids=checkpoint.transaction_ids,
                summary=summary,
            )
        except Exception as exc:
            self.event_store.append(
                session_id=source_session,
                event_type=EventType.REWIND_FAILED,
                payload={
                    "rewind_id": rewind_id,
                    "checkpoint_id": checkpoint_id,
                    "mode": selected_mode.value,
                    "restore_id": restore_id,
                    "code_restore_completed": bool(restore_id),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

    def describe(self, checkpoint: PromptCheckpoint) -> dict[str, Any]:
        return asdict(checkpoint)

    def _find_checkpoint(self, session_id: str, checkpoint_id: str) -> PromptCheckpoint:
        checkpoints = self.list_checkpoints(session_id)
        for checkpoint in checkpoints:
            if checkpoint.checkpoint_id == checkpoint_id:
                return checkpoint
        if checkpoint_id.lower() == "latest" and checkpoints:
            return checkpoints[-1]
        if checkpoint_id.isdigit():
            index = int(checkpoint_id)
            if 1 <= index <= len(checkpoints):
                return checkpoints[index - 1]
        raise ValueError(f"Unknown checkpoint: {checkpoint_id}")

    @staticmethod
    def _transactions_after(events: list[AgentEvent], sequence: int) -> tuple[str, ...]:
        values: list[str] = []
        for event in reversed(events):
            if event.sequence <= sequence or event.event_type != EventType.TOOL_COMPLETED:
                continue
            transaction_id = event.payload.get("transaction_id")
            if isinstance(transaction_id, str) and transaction_id not in values:
                values.append(transaction_id)
        return tuple(values)

    def _summarize_after(self, session_id: str, sequence: int) -> str:
        events = [
            event for event in self.event_store.list_events(session_id)
            if event.sequence > sequence and event.event_type not in {
                EventType.REWIND_REQUESTED,
                EventType.REWIND_COMPLETED,
                EventType.REWIND_FAILED,
            }
        ]
        lines = []
        for event in events:
            if event.event_type == EventType.TURN_STARTED:
                lines.append(f"User: {event.payload.get('input', '')}")
            elif event.event_type == EventType.MODEL_RESPONDED and event.payload.get("text"):
                lines.append(f"Assistant: {event.payload.get('text', '')}")
            elif event.event_type == EventType.TOOL_COMPLETED:
                lines.append(f"Tool completed: {event.payload.get('tool_name', '')}")
        summary = "Rewound-history summary:\n" + "\n".join(lines)
        summary = summary[:8000]
        if events:
            self.event_store.append(
                session_id=session_id,
                event_type=EventType.CONTEXT_COMPACTED,
                payload={
                    "reason": "checkpoint_summary",
                    "shadowed_event_ids": [event.event_id for event in events],
                    "summary_messages": [{"role": "assistant", "content": summary}],
                },
            )
            if self.context is not None:
                self.context.messages = project_messages(
                    self.event_store.list_events(session_id)
                )
        return summary

    def _active_session_id(self) -> str:
        if self.context is None:
            raise ValueError("session_id is required without an active conversation context")
        return self.context.session_id
