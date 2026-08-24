"""Validation helpers for completed runtime lifecycle event sequences."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from undomesh.runtime.events import AgentEvent, EventType


class LifecycleInvariantError(ValueError):
    """Raised when a runtime event sequence cannot represent a valid lifecycle."""


TURN_TERMINALS = {
    EventType.TURN_ENDED,
    EventType.TURN_FAILED,
    EventType.TURN_CANCELLED,
}
STEP_TERMINALS = {EventType.STEP_ENDED, EventType.STEP_FAILED}


def validate_lifecycle(events: Sequence[AgentEvent]) -> None:
    """Validate ordering, closure, and tool-call pairing for completed events."""
    if not events:
        return

    sessions = {event.session_id for event in events}
    if len(sessions) != 1:
        raise LifecycleInvariantError("A lifecycle validation batch must contain one session")

    sequences = [event.sequence for event in events]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise LifecycleInvariantError("Event sequences must be unique and increasing")

    turn_events: dict[str, list[AgentEvent]] = defaultdict(list)
    step_events: dict[str, list[AgentEvent]] = defaultdict(list)
    for event in events:
        if event.turn_id:
            turn_events[event.turn_id].append(event)
        if event.step_id:
            step_events[event.step_id].append(event)

    for turn_id, related in turn_events.items():
        types = [event.event_type for event in related]
        if types.count(EventType.TURN_STARTED) != 1:
            raise LifecycleInvariantError(f"Turn {turn_id} must start exactly once")
        terminals = sum(types.count(event_type) for event_type in TURN_TERMINALS)
        if terminals != 1 or types[-1] not in TURN_TERMINALS:
            raise LifecycleInvariantError(f"Turn {turn_id} must have one final terminal event")

    for step_id, related in step_events.items():
        types = [event.event_type for event in related]
        if types.count(EventType.STEP_STARTED) != 1:
            raise LifecycleInvariantError(f"Step {step_id} must start exactly once")
        terminals = sum(types.count(event_type) for event_type in STEP_TERMINALS)
        if terminals != 1:
            raise LifecycleInvariantError(f"Step {step_id} must have one terminal event")

    call_events: dict[str, list[EventType]] = defaultdict(list)
    for event in events:
        if not event.event_type.value.startswith("tool."):
            continue
        call_id = event.payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            raise LifecycleInvariantError("Every tool event must carry a call_id")
        call_events[call_id].append(event.event_type)

    for call_id, types in call_events.items():
        counts = Counter(types)
        if counts[EventType.TOOL_REQUESTED] != 1:
            raise LifecycleInvariantError(f"Tool call {call_id} must be requested once")
        if counts[EventType.TOOL_STARTED] != 1:
            raise LifecycleInvariantError(f"Tool call {call_id} must start once")
        terminals = (
            counts[EventType.TOOL_COMPLETED]
            + counts[EventType.TOOL_FAILED]
            + counts[EventType.TOOL_DENIED]
        )
        if terminals != 1:
            raise LifecycleInvariantError(f"Tool call {call_id} must have one terminal event")
        if types[0] != EventType.TOOL_REQUESTED or types[1] != EventType.TOOL_STARTED:
            raise LifecycleInvariantError(f"Tool call {call_id} has invalid ordering")
