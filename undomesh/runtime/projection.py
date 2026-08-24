"""Derive model-visible conversation state from immutable runtime events."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from undomesh.llm.message_builder import MessageBuilder
from undomesh.runtime.events import AgentEvent, EventType


def project_messages(events: Sequence[AgentEvent]) -> list[dict[str, Any]]:
    """Build OpenAI-compatible history without executing recorded side effects."""
    shadowed: set[str] = set()
    summaries_at: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.event_type == EventType.CONTEXT_COMPACTED:
            ids = event.payload.get("shadowed_event_ids", [])
            if isinstance(ids, list):
                valid_ids = [item for item in ids if isinstance(item, str)]
                shadowed.update(valid_ids)
                summary_messages = event.payload.get("summary_messages", [])
                if valid_ids and isinstance(summary_messages, list):
                    summaries_at[valid_ids[0]] = [
                        item for item in summary_messages if isinstance(item, dict)
                    ]
    messages: list[dict[str, Any]] = []
    for event in events:
        if event.event_id in shadowed:
            messages.extend(summaries_at.get(event.event_id, []))
            continue
        if event.event_type == EventType.CONTEXT_COMPACTED:
            continue
        if event.event_type == EventType.TURN_STARTED:
            content = event.payload.get("input")
            if isinstance(content, str):
                messages.append({"role": "user", "content": content})
        elif event.event_type == EventType.MODEL_RESPONDED:
            text = event.payload.get("text", "")
            tool_calls = event.payload.get("tool_calls", [])
            if isinstance(tool_calls, list) and tool_calls:
                messages.append(MessageBuilder.build_assistant_with_tool_use(text, tool_calls))
            elif isinstance(text, str):
                messages.append({"role": "assistant", "content": text})
        elif event.event_type in {
            EventType.TOOL_COMPLETED,
            EventType.TOOL_FAILED,
            EventType.TOOL_DENIED,
        }:
            call_id = event.payload.get("call_id")
            if not isinstance(call_id, str):
                continue
            content = event.payload.get("content")
            if not isinstance(content, str):
                content = event.payload.get("reason") or event.payload.get("message") or "Tool failed"
            if event.event_type in {EventType.TOOL_FAILED, EventType.TOOL_DENIED} and not content.startswith("Error:"):
                content = f"Error: {content}"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
    return messages
