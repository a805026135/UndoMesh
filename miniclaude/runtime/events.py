"""Immutable events emitted by the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from miniclaude.provenance.models import Provenance


class EventType(StrEnum):
    """Event types supported by the first runtime lifecycle iteration."""

    SESSION_STARTED = "session.started"
    SESSION_RESUMED = "session.resumed"
    SESSION_FORKED = "session.forked"
    TURN_STARTED = "turn.started"
    TURN_ENDED = "turn.ended"
    TURN_FAILED = "turn.failed"
    TURN_CANCELLED = "turn.cancelled"
    STEP_STARTED = "step.started"
    STEP_ENDED = "step.ended"
    STEP_FAILED = "step.failed"
    MODEL_REQUESTED = "model.requested"
    MODEL_RESPONDED = "model.responded"
    MODEL_FAILED = "model.failed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_DENIED = "tool.denied"
    CONTEXT_COMPACTED = "context.compacted"
    CONTEXT_COMPACTION_STARTED = "context.compaction_started"
    CONTEXT_COMPACTION_FAILED = "context.compaction_failed"
    SKILL_ROUTED = "skill.routed"
    SKILL_EXECUTED = "skill.executed"
    AGENT_SPAWNED = "agent.spawned"
    AGENT_COMPLETED = "agent.completed"
    AGENT_CANCELLED = "agent.cancelled"
    REWIND_REQUESTED = "rewind.requested"
    REWIND_COMPLETED = "rewind.completed"
    REWIND_FAILED = "rewind.failed"
    SESSION_REWOUND = "session.rewound"
    HOOK_INVOKED = "hook.invoked"
    HOOK_COMPLETED = "hook.completed"
    HOOK_FAILED = "hook.failed"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """A single immutable runtime fact.

    Sequence numbers are assigned by the event store and are monotonically
    increasing within one session.
    """

    event_id: str
    session_id: str
    turn_id: str | None
    step_id: str | None
    sequence: int
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None
