from __future__ import annotations

from pathlib import Path
import subprocess

from undomesh.core.context import ConversationContext
from undomesh.execution.patches import PatchSet
from undomesh.execution.transactions import DurableChangeEngine
from undomesh.runtime.checkpoints import CheckpointManager, RewindMode
from undomesh.runtime.events import EventType
from undomesh.runtime.projection import project_messages
from undomesh.runtime.store import InMemoryEventStore


class FailingForkStore(InMemoryEventStore):
    def fork_session(self, *args, **kwargs):
        raise OSError("simulated event-store failure")


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _fixture(config, tmp_path: Path):
    _repo(tmp_path)
    target = tmp_path / "checkpoint.txt"
    target.write_text("zero\n", encoding="utf-8")
    engine = DurableChangeEngine(tmp_path)
    store = InMemoryEventStore()
    context = ConversationContext(config)
    session = context.session_id
    store.append(session_id=session, event_type=EventType.SESSION_STARTED)
    first_turn = store.append(
        session_id=session, event_type=EventType.TURN_STARTED, turn_id="turn-1",
        payload={"input": "change zero to one"},
    )
    first = engine.apply(PatchSet.parse(
        "--- a/checkpoint.txt\n+++ b/checkpoint.txt\n@@ -1 +1 @@\n-zero\n+one\n"
    ))
    store.append(
        session_id=session, event_type=EventType.TOOL_COMPLETED, turn_id="turn-1",
        payload={"transaction_id": first.transaction_id, "tool_name": "apply_patch_set", "call_id": "1"},
    )
    store.append(session_id=session, event_type=EventType.TURN_ENDED, turn_id="turn-1")
    second_turn = store.append(
        session_id=session, event_type=EventType.TURN_STARTED, turn_id="turn-2",
        payload={"input": "change one to two"},
    )
    second = engine.apply(PatchSet.parse(
        "--- a/checkpoint.txt\n+++ b/checkpoint.txt\n@@ -1 +1 @@\n-one\n+two\n"
    ))
    store.append(
        session_id=session, event_type=EventType.TOOL_COMPLETED, turn_id="turn-2",
        payload={"transaction_id": second.transaction_id, "tool_name": "apply_patch_set", "call_id": "2"},
    )
    store.append(session_id=session, event_type=EventType.TURN_ENDED, turn_id="turn-2")
    return target, engine, store, context, first_turn, second_turn, first, second


def test_checkpoint_lists_transaction_closure_and_health(config, tmp_path: Path) -> None:
    target, engine, store, context, first_turn, _, first, second = _fixture(config, tmp_path)
    checkpoints = CheckpointManager(store, engine, context).list_checkpoints()

    assert target.read_text(encoding="utf-8") == "two\n"
    assert checkpoints[0].checkpoint_id == first_turn.event_id
    assert checkpoints[0].transaction_ids == (second.transaction_id, first.transaction_id)
    assert checkpoints[0].code_health == "available"
    assert checkpoints[1].transaction_ids == (second.transaction_id,)


def test_both_rewind_restores_code_and_forks_conversation(config, tmp_path: Path) -> None:
    target, engine, store, context, first_turn, _, first, second = _fixture(config, tmp_path)
    source_session = context.session_id
    source_count = len(store.list_events(source_session))
    manager = CheckpointManager(store, engine, context)

    receipt = manager.rewind(first_turn.event_id, RewindMode.BOTH)

    assert target.read_text(encoding="utf-8") == "zero\n"
    assert receipt.restore_id
    assert receipt.active_session_id != source_session
    assert context.session_id == receipt.active_session_id
    assert len(store.list_events(source_session)) == source_count + 2
    messages = project_messages(store.list_events(receipt.active_session_id))
    assert messages[0] == {"role": "user", "content": "change zero to one"}
    assert engine.status(first.transaction_id).state.value == "committed"
    assert engine.status(second.transaction_id).state.value == "committed"


def test_conversation_only_rewind_does_not_touch_code(config, tmp_path: Path) -> None:
    target, engine, store, context, _, second_turn, *_ = _fixture(config, tmp_path)
    receipt = CheckpointManager(store, engine, context).rewind(
        second_turn.event_id, RewindMode.CONVERSATION
    )

    assert not receipt.restore_id
    assert target.read_text(encoding="utf-8") == "two\n"
    assert context.session_id == receipt.active_session_id


def test_summary_rewind_preserves_events_and_changes_projection(config, tmp_path: Path) -> None:
    _, engine, store, context, first_turn, *_ = _fixture(config, tmp_path)
    manager = CheckpointManager(store, engine, context)
    before = len(store.list_events(context.session_id))

    receipt = manager.rewind(first_turn.event_id, RewindMode.SUMMARY)
    events = store.list_events(context.session_id)
    messages = project_messages(events)

    assert receipt.summary.startswith("Rewound-history summary:")
    assert len(events) > before
    assert any(event.event_type == EventType.CONTEXT_COMPACTED for event in events)
    assert any("Rewound-history summary" in str(message.get("content")) for message in messages)


def test_code_rewind_refuses_unknown_newer_content_before_writing(config, tmp_path: Path) -> None:
    target, engine, store, context, first_turn, *_ = _fixture(config, tmp_path)
    manager = CheckpointManager(store, engine, context)
    target.write_text("manual\n", encoding="utf-8")

    try:
        manager.rewind(first_turn.event_id, RewindMode.CODE)
    except RuntimeError as exc:
        assert "not safely restorable" in str(exc)
    else:
        raise AssertionError("conflicted rewind unexpectedly succeeded")

    assert target.read_text(encoding="utf-8") == "manual\n"
    assert any(
        event.event_type == EventType.REWIND_FAILED
        for event in store.list_events(context.session_id)
    )


def test_both_rewind_audits_partial_code_restore_if_fork_fails(config, tmp_path: Path) -> None:
    target, engine, original, context, first_turn, *_ = _fixture(config, tmp_path)
    store = FailingForkStore()
    for event in original.list_events(context.session_id):
        store._events.setdefault(context.session_id, []).append(event)
    manager = CheckpointManager(store, engine, context)

    try:
        manager.rewind(first_turn.event_id, RewindMode.BOTH)
    except OSError as exc:
        assert "event-store failure" in str(exc)
    else:
        raise AssertionError("fork failure unexpectedly succeeded")

    assert target.read_text(encoding="utf-8") == "zero\n"
    failure = [
        event for event in store.list_events(context.session_id)
        if event.event_type == EventType.REWIND_FAILED
    ][-1]
    assert failure.payload["restore_id"]
    assert failure.payload["code_restore_completed"] is True
