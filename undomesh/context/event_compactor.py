"""Event-backed context compaction that preserves the original session log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from undomesh.llm.token_counter import count_message_tokens
from undomesh.runtime.events import AgentEvent, EventType
from undomesh.runtime.projection import project_messages
from undomesh.runtime.store import EventStore


@dataclass(frozen=True, slots=True)
class CompactionResult:
    compaction_id: str
    shadowed_event_ids: tuple[str, ...]
    original_tokens: int
    projected_tokens: int
    summary: str


class EventCompactor:
    """Shadow a balanced closed prefix and append a structured summary event."""

    def __init__(self, store: EventStore, retain_steps: int = 2) -> None:
        self.store = store
        self.retain_steps = retain_steps

    def compact_if_needed(
        self,
        session_id: str,
        *,
        target_tokens: int,
    ) -> CompactionResult | None:
        events = self.store.list_events(session_id)
        messages = project_messages(events)
        original_tokens = count_message_tokens(messages)
        if original_tokens <= target_tokens:
            return None

        completed_step_ids = [
            event.step_id
            for event in events
            if event.event_type in {EventType.STEP_ENDED, EventType.STEP_FAILED} and event.step_id
        ]
        if not completed_step_ids:
            return None
        # Under pressure at least one closed step must be compacted.  The
        # currently open step is never selected, so retry state stays intact.
        shadow_count = max(1, len(completed_step_ids) - self.retain_steps)
        retained = set(completed_step_ids[shadow_count:])
        already_shadowed = {
            event_id
            for event in events
            if event.event_type == EventType.CONTEXT_COMPACTED
            for event_id in event.payload.get("shadowed_event_ids", [])
            if isinstance(event_id, str)
        }
        if retained:
            first_retained_sequence = min(
                event.sequence for event in events if event.step_id in retained
            )
        else:
            closed = set(completed_step_ids)
            open_steps = [
                event.sequence
                for event in events
                if event.event_type == EventType.STEP_STARTED and event.step_id not in closed
            ]
            first_retained_sequence = min(open_steps, default=events[-1].sequence + 1)
        shadowable = [
            event
            for event in events
            if event.sequence < first_retained_sequence
            and event.event_id not in already_shadowed
            and not event.event_type.value.startswith("session.")
            and event.event_type
            not in {
                EventType.CONTEXT_COMPACTION_STARTED,
                EventType.CONTEXT_COMPACTED,
                EventType.CONTEXT_COMPACTION_FAILED,
            }
        ]
        if not shadowable:
            return None

        compaction_id = str(uuid4())
        summary = self._structured_summary(project_messages(shadowable))
        self.store.append(
            session_id=session_id,
            event_type=EventType.CONTEXT_COMPACTION_STARTED,
            payload={
                "compaction_id": compaction_id,
                "original_tokens": original_tokens,
                "target_tokens": target_tokens,
            },
        )
        self.store.append(
            session_id=session_id,
            event_type=EventType.CONTEXT_COMPACTED,
            payload={
                "compaction_id": compaction_id,
                "shadowed_event_ids": [event.event_id for event in shadowable],
                "shadowed_sequences": [event.sequence for event in shadowable],
                "summary": summary,
                "summary_messages": [{"role": "assistant", "content": summary}],
                "original_tokens": original_tokens,
            },
        )
        projected_tokens = count_message_tokens(project_messages(self.store.list_events(session_id)))
        return CompactionResult(
            compaction_id=compaction_id,
            shadowed_event_ids=tuple(event.event_id for event in shadowable),
            original_tokens=original_tokens,
            projected_tokens=projected_tokens,
            summary=summary,
        )

    @staticmethod
    def _structured_summary(messages: list[dict[str, Any]]) -> str:
        users = [str(message.get("content", "")) for message in messages if message.get("role") == "user"]
        assistants = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "assistant" and message.get("content")
        ]
        tool_calls = [message for message in messages if message.get("role") == "tool"]
        goal = users[0][:300] if users else "Unknown"
        latest = users[-1][:300] if users else "Unknown"
        decision = assistants[-1][:500] if assistants else "No recorded assistant conclusion"
        return (
            "[Structured context summary]\n"
            f"User goal: {goal}\n"
            f"Latest historical request: {latest}\n"
            f"Last assistant conclusion: {decision}\n"
            f"Historical tool results: {len(tool_calls)}\n"
            "Original facts remain available in the append-only event log."
        )
