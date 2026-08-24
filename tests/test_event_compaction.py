"""Stage 5: event-backed context compaction invariants."""

from __future__ import annotations

from undomesh.context.event_compactor import EventCompactor
from undomesh.runtime.events import EventType
from undomesh.runtime.projection import project_messages
from undomesh.runtime.store import InMemoryEventStore


def _closed_tool_step(store: InMemoryEventStore, session: str, index: int) -> None:
    step = f"step-{index}"
    call = f"call-{index}"
    store.append(session_id=session, event_type=EventType.STEP_STARTED, step_id=step)
    store.append(
        session_id=session,
        event_type=EventType.MODEL_RESPONDED,
        step_id=step,
        payload={"text": "", "tool_calls": [{"id": call, "name": "read_file", "input": {}}]},
    )
    store.append(
        session_id=session,
        event_type=EventType.TOOL_COMPLETED,
        step_id=step,
        payload={"call_id": call, "content": "x" * 800},
    )
    store.append(session_id=session, event_type=EventType.STEP_ENDED, step_id=step)


def test_compaction_preserves_raw_log_and_tool_pairing() -> None:
    store = InMemoryEventStore()
    session = "compact"
    store.append(
        session_id=session,
        event_type=EventType.TURN_STARTED,
        payload={"input": "inspect the project"},
    )
    for index in range(4):
        _closed_tool_step(store, session, index)
    raw = store.list_events(session)

    result = EventCompactor(store, retain_steps=2).compact_if_needed(
        session, target_tokens=20
    )

    assert result is not None
    assert store.list_events(session)[: len(raw)] == raw
    assert len(store.list_events(session)) == len(raw) + 2
    projected = project_messages(store.list_events(session))
    assert projected[0]["content"].startswith("[Structured context summary]")
    assistant_calls = {
        call["id"]
        for message in projected
        for call in message.get("tool_calls", [])
    }
    tool_results = {
        message["tool_call_id"] for message in projected if message["role"] == "tool"
    }
    assert assistant_calls == tool_results == {"call-2", "call-3"}


def test_single_closed_step_can_be_compacted_before_open_step() -> None:
    store = InMemoryEventStore()
    session = "overflow"
    store.append(session_id=session, event_type=EventType.TURN_STARTED, payload={"input": "old"})
    _closed_tool_step(store, session, 1)
    store.append(session_id=session, event_type=EventType.STEP_STARTED, step_id="current")

    result = EventCompactor(store).compact_if_needed(session, target_tokens=10)

    assert result is not None
    assert any(
        event.step_id == "current" and event.event_id not in result.shadowed_event_ids
        for event in store.list_events(session)
    )
