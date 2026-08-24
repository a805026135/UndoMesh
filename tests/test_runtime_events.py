"""Invariants for the initial append-only runtime event store."""

from __future__ import annotations

import pytest

from miniclaude.runtime.dispatcher import LifecycleDispatcher, ListenerFailurePolicy
from miniclaude.runtime.events import EventType
from miniclaude.runtime.invariants import LifecycleInvariantError, validate_lifecycle
from miniclaude.runtime.store import InMemoryEventStore


def test_sequences_are_monotonic_per_session():
    store = InMemoryEventStore()

    first = store.append(session_id="one", event_type=EventType.TURN_STARTED)
    second = store.append(session_id="one", event_type=EventType.TURN_ENDED)
    other = store.append(session_id="two", event_type=EventType.TURN_STARTED)

    assert (first.sequence, second.sequence) == (1, 2)
    assert other.sequence == 1
    assert len({first.event_id, second.event_id, other.event_id}) == 3


def test_list_events_returns_a_snapshot():
    store = InMemoryEventStore()
    store.append(session_id="session", event_type=EventType.TURN_STARTED)

    snapshot = store.list_events("session")
    snapshot.clear()

    assert len(store.list_events("session")) == 1


def test_dispatcher_runs_typed_then_catch_all_and_can_unsubscribe():
    store = InMemoryEventStore()
    dispatcher = LifecycleDispatcher()
    observed: list[str] = []
    unsubscribe = dispatcher.subscribe(lambda event: observed.append("all"))
    dispatcher.subscribe(
        lambda event: observed.append("typed"),
        event_type=EventType.TURN_STARTED,
    )
    event = store.append(session_id="session", event_type=EventType.TURN_STARTED)

    dispatcher.publish(event)
    unsubscribe()
    dispatcher.publish(event)

    assert observed == ["typed", "all", "typed"]


def test_dispatcher_logs_observer_failure_and_continues(caplog):
    store = InMemoryEventStore()
    dispatcher = LifecycleDispatcher()

    def broken(event):
        raise RuntimeError("observer failed")

    dispatcher.subscribe(broken, name="broken-observer")
    event = store.append(session_id="session", event_type=EventType.TURN_STARTED)

    dispatcher.publish(event)

    assert "broken-observer" in caplog.text


def test_dispatcher_can_raise_for_explicit_strict_listener():
    store = InMemoryEventStore()
    dispatcher = LifecycleDispatcher()

    def broken(event):
        raise RuntimeError("strict failure")

    dispatcher.subscribe(
        broken,
        failure_policy=ListenerFailurePolicy.RAISE,
        name="strict-listener",
    )
    event = store.append(session_id="session", event_type=EventType.TURN_STARTED)

    with pytest.raises(RuntimeError, match="strict failure"):
        dispatcher.publish(event)


def test_lifecycle_validator_rejects_unclosed_turn():
    store = InMemoryEventStore()
    store.append(
        session_id="session",
        event_type=EventType.TURN_STARTED,
        turn_id="turn",
    )

    with pytest.raises(LifecycleInvariantError, match="terminal"):
        validate_lifecycle(store.list_events("session"))
