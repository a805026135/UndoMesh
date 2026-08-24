"""Durable session, projection, fork and artifact tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from miniclaude.llm.client import LLMResponse
from miniclaude.runtime.artifacts import ArtifactIntegrityError, ArtifactStore
from miniclaude.runtime.events import EventType
from miniclaude.runtime.replay import ReplayEngine, ReplayMode
from miniclaude.runtime.store import SQLiteEventStore
from test_query_loop_integration import make_loop


@pytest.mark.asyncio
async def test_sqlite_store_survives_reopen_and_rebuilds_messages(config, tmp_path):
    db_path = tmp_path / "events.db"
    store = SQLiteEventStore(db_path)
    query_loop, _, _ = make_loop(
        config,
        [
            LLMResponse(
                tool_calls=[{"id": "call-1", "name": "echo", "input": {"text": "persisted"}}],
                stop_reason="tool_calls",
            ),
            LLMResponse(text="finished", stop_reason="stop"),
        ],
        event_store=store,
    )

    assert await query_loop.run("persist this") == "finished"
    session_id = query_loop.context.session_id
    expected_count = len(query_loop.events)
    store.close()

    reopened = SQLiteEventStore(db_path)
    replay = ReplayEngine(reopened).replay(session_id, ReplayMode.DRY_RUN)

    assert replay.event_count == expected_count
    assert not replay.side_effects_executed
    assert replay.messages[0] == {"role": "user", "content": "persist this"}
    assert any(message.get("role") == "tool" and message["content"] == "persisted" for message in replay.messages)
    assert replay.messages[-1] == {"role": "assistant", "content": "finished"}
    reopened.close()


def test_fork_copies_only_requested_history_and_records_origin(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.db")
    store.append(session_id="source", event_type=EventType.SESSION_STARTED)
    store.append(
        session_id="source",
        event_type=EventType.TURN_STARTED,
        turn_id="turn-1",
        payload={"input": "first"},
    )
    store.append(
        session_id="source",
        event_type=EventType.TURN_ENDED,
        turn_id="turn-1",
    )
    store.append(
        session_id="source",
        event_type=EventType.TURN_STARTED,
        turn_id="turn-2",
        payload={"input": "excluded"},
    )

    target = ReplayEngine(store).fork(
        "source",
        through_sequence=3,
        new_session_id="fork",
    )
    events = store.list_events(target)

    assert [event.event_type for event in events] == [
        EventType.SESSION_STARTED,
        EventType.TURN_STARTED,
        EventType.TURN_ENDED,
        EventType.SESSION_FORKED,
    ]
    assert events[-1].payload["source_session_id"] == "source"
    assert all("excluded" not in str(event.payload) for event in events)
    store.close()


def test_content_addressed_artifact_detects_tampering(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.put_text("important output", metadata={"tool": "test"})

    assert store.read_text(ref.artifact_id) == "important output"
    ref.path.write_text("tampered", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        store.read_text(ref.artifact_id)


def test_sqlite_redacts_common_secrets_before_persistence(tmp_path):
    path = tmp_path / "events.db"
    store = SQLiteEventStore(path)
    store.append(
        session_id="secret-session",
        event_type=EventType.TURN_STARTED,
        payload={"input": "API_KEY=very-secret", "token": "raw-token"},
    )
    store.close()

    reopened = SQLiteEventStore(path)
    payload = reopened.list_events("secret-session")[0].payload

    assert "very-secret" not in str(payload)
    assert "raw-token" not in str(payload)
    assert "[REDACTED]" in str(payload)
    reopened.close()


def test_schema_migration_adds_provenance_to_legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT,
            step_id TEXT, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL, payload_json TEXT NOT NULL,
            UNIQUE(session_id, sequence)
        )
        """
    )
    connection.commit()
    connection.close()

    store = SQLiteEventStore(path)
    columns = {
        row[1] for row in store._connection.execute("PRAGMA table_info(events)").fetchall()
    }

    assert "provenance_json" in columns
    store.close()
