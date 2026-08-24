"""Event store contracts and the initial in-memory implementation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol
from uuid import uuid4

from undomesh.runtime.events import AgentEvent, EventType
from undomesh.provenance.models import Provenance
from undomesh.runtime.redaction import redact_payload


class EventStore(Protocol):
    """Append-only storage contract for runtime events."""

    def append(
        self,
        *,
        session_id: str,
        event_type: EventType,
        turn_id: str | None = None,
        step_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        provenance: Provenance | None = None,
    ) -> AgentEvent: ...

    def list_events(self, session_id: str) -> list[AgentEvent]: ...

    def fork_session(
        self, source_session_id: str, new_session_id: str, *, through_sequence: int | None = None
    ) -> str: ...


class InMemoryEventStore:
    """Append-only event store used until durable storage is introduced."""

    def __init__(self) -> None:
        self._events: dict[str, list[AgentEvent]] = defaultdict(list)

    def append(
        self,
        *,
        session_id: str,
        event_type: EventType,
        turn_id: str | None = None,
        step_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        provenance: Provenance | None = None,
    ) -> AgentEvent:
        session_events = self._events[session_id]
        event = AgentEvent(
            event_id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            step_id=step_id,
            sequence=len(session_events) + 1,
            event_type=event_type,
            payload=dict(payload or {}),
            provenance=provenance,
        )
        session_events.append(event)
        return event

    def list_events(self, session_id: str) -> list[AgentEvent]:
        """Return a snapshot so callers cannot mutate the stored list."""
        return list(self._events.get(session_id, ()))

    def fork_session(
        self,
        source_session_id: str,
        new_session_id: str,
        *,
        through_sequence: int | None = None,
    ) -> str:
        source = self.list_events(source_session_id)
        if through_sequence is not None:
            source = [event for event in source if event.sequence <= through_sequence]
        boundary = source[-1].sequence if source else 0
        for original in source:
            self.append(
                session_id=new_session_id,
                event_type=original.event_type,
                turn_id=original.turn_id,
                step_id=original.step_id,
                payload={**original.payload, "replayed_from_event_id": original.event_id},
                provenance=original.provenance,
            )
        self.append(
            session_id=new_session_id,
            event_type=EventType.SESSION_FORKED,
            payload={"source_session_id": source_session_id, "through_sequence": boundary},
        )
        return new_session_id

    def list_sessions(self) -> list[str]:
        return list(self._events)


class SQLiteEventStore:
    """Durable append-only event store with a versioned SQLite schema."""

    SCHEMA_VERSION = 2

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    forked_from TEXT,
                    forked_through_sequence INTEGER
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT,
                    step_id TEXT,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    provenance_json TEXT,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_session_sequence
                    ON events(session_id, sequence);
                """
            )
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(events)").fetchall()
            }
            if "provenance_json" not in columns:
                self._connection.execute("ALTER TABLE events ADD COLUMN provenance_json TEXT")
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (self.SCHEMA_VERSION, datetime.now().astimezone().isoformat()),
            )

    def ensure_session(
        self,
        session_id: str,
        *,
        forked_from: str | None = None,
        forked_through_sequence: int | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    session_id, created_at, forked_from, forked_through_sequence
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    datetime.now().astimezone().isoformat(),
                    forked_from,
                    forked_through_sequence,
                ),
            )

    def append(
        self,
        *,
        session_id: str,
        event_type: EventType,
        turn_id: str | None = None,
        step_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        provenance: Provenance | None = None,
    ) -> AgentEvent:
        self.ensure_session(session_id)
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        event = AgentEvent(
            event_id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            step_id=step_id,
            sequence=int(row["next_sequence"]),
            event_type=event_type,
            payload=dict(payload or {}),
            provenance=provenance,
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO events(
                    event_id, session_id, turn_id, step_id, sequence,
                    event_type, timestamp, payload_json, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.turn_id,
                    event.step_id,
                    event.sequence,
                    event.event_type.value,
                    event.timestamp.isoformat(),
                    json.dumps(redact_payload(event.payload), ensure_ascii=False, default=str),
                    json.dumps(event.provenance.to_dict(), ensure_ascii=False)
                    if event.provenance
                    else None,
                ),
            )
        return event

    def list_events(self, session_id: str) -> list[AgentEvent]:
        rows = self._connection.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        ).fetchall()
        return [
            AgentEvent(
                event_id=row["event_id"],
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                step_id=row["step_id"],
                sequence=row["sequence"],
                event_type=EventType(row["event_type"]),
                timestamp=datetime.fromisoformat(row["timestamp"]),
                payload=json.loads(row["payload_json"]),
                provenance=Provenance.from_dict(json.loads(row["provenance_json"]))
                if row["provenance_json"]
                else None,
            )
            for row in rows
        ]

    def fork_session(
        self,
        source_session_id: str,
        new_session_id: str,
        *,
        through_sequence: int | None = None,
    ) -> str:
        source = self.list_events(source_session_id)
        if through_sequence is not None:
            source = [event for event in source if event.sequence <= through_sequence]
        boundary = source[-1].sequence if source else 0
        self.ensure_session(
            new_session_id,
            forked_from=source_session_id,
            forked_through_sequence=boundary,
        )
        for original in source:
            self.append(
                session_id=new_session_id,
                event_type=original.event_type,
                turn_id=original.turn_id,
                step_id=original.step_id,
                payload={**original.payload, "replayed_from_event_id": original.event_id},
                provenance=original.provenance,
            )
        self.append(
            session_id=new_session_id,
            event_type=EventType.SESSION_FORKED,
            payload={
                "source_session_id": source_session_id,
                "through_sequence": boundary,
            },
        )
        return new_session_id

    def list_sessions(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT session_id FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [str(row["session_id"]) for row in rows]

    def close(self) -> None:
        self._connection.close()
