"""Durable single-writer command queue for long-running application work."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    kind: str
    state: JobState
    payload: dict[str, Any]
    result: dict[str, Any] | None
    progress: float
    message: str
    cancel_requested: bool
    created_at: float
    updated_at: float


class JobStore:
    """SQLite job journal. Running jobs become interrupted after a restart."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
            payload TEXT NOT NULL, result TEXT, progress REAL NOT NULL,
            message TEXT NOT NULL, cancel_requested INTEGER NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL)"""
        )
        self._db.commit()

    def recover_interrupted(self) -> int:
        now = time.time()
        cursor = self._db.execute(
            "UPDATE jobs SET state=?, message=?, updated_at=? WHERE state=?",
            (JobState.INTERRUPTED, "process stopped while job was running", now, JobState.RUNNING),
        )
        self._db.commit()
        return cursor.rowcount

    def create(self, kind: str, payload: dict[str, Any]) -> JobRecord:
        now = time.time()
        job_id = f"job_{uuid4().hex}"
        self._db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, NULL, 0, '', 0, ?, ?)",
            (job_id, kind, JobState.QUEUED, json.dumps(payload), now, now),
        )
        self._db.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> JobRecord:
        row = self._db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def list(self, *, after: float = 0) -> list[JobRecord]:
        rows = self._db.execute(
            "SELECT * FROM jobs WHERE updated_at>? ORDER BY created_at, job_id", (after,)
        ).fetchall()
        return [self._record(row) for row in rows]

    def claim_next(self) -> JobRecord | None:
        self._db.execute("BEGIN IMMEDIATE")
        row = self._db.execute(
            "SELECT job_id FROM jobs WHERE state=? ORDER BY created_at LIMIT 1",
            (JobState.QUEUED,),
        ).fetchone()
        if row is None:
            self._db.commit()
            return None
        now = time.time()
        self._db.execute(
            "UPDATE jobs SET state=?, updated_at=? WHERE job_id=?",
            (JobState.RUNNING, now, row["job_id"]),
        )
        self._db.commit()
        return self.get(row["job_id"])

    def update_progress(self, job_id: str, progress: float, message: str = "") -> None:
        self._db.execute(
            "UPDATE jobs SET progress=?, message=?, updated_at=? WHERE job_id=? AND state=?",
            (max(0.0, min(1.0, progress)), message, time.time(), job_id, JobState.RUNNING),
        )
        self._db.commit()

    def request_cancel(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.state == JobState.QUEUED:
            self._finish(job_id, JobState.CANCELLED, None, "cancelled before execution")
        elif job.state == JobState.RUNNING:
            self._db.execute(
                "UPDATE jobs SET cancel_requested=1, updated_at=? WHERE job_id=?",
                (time.time(), job_id),
            )
            self._db.commit()
        return self.get(job_id)

    def resume(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.state != JobState.INTERRUPTED:
            raise ValueError("only interrupted jobs may be resumed")
        self._db.execute(
            "UPDATE jobs SET state=?, cancel_requested=0, message='', updated_at=? WHERE job_id=?",
            (JobState.QUEUED, time.time(), job_id),
        )
        self._db.commit()
        return self.get(job_id)

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        self._finish(job_id, JobState.SUCCEEDED, result, "completed")

    def fail(self, job_id: str, message: str) -> None:
        self._finish(job_id, JobState.FAILED, None, message)

    def cancel_running(self, job_id: str) -> None:
        self._finish(job_id, JobState.CANCELLED, None, "cancelled")

    def close(self) -> None:
        self._db.close()

    def _finish(self, job_id: str, state: JobState, result: dict[str, Any] | None, message: str) -> None:
        self._db.execute(
            "UPDATE jobs SET state=?, result=?, progress=?, message=?, updated_at=? WHERE job_id=?",
            (state, json.dumps(result) if result is not None else None,
             1.0 if state == JobState.SUCCEEDED else 0.0, message, time.time(), job_id),
        )
        self._db.commit()

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"], kind=row["kind"], state=JobState(row["state"]),
            payload=json.loads(row["payload"]),
            result=json.loads(row["result"]) if row["result"] else None,
            progress=float(row["progress"]), message=row["message"],
            cancel_requested=bool(row["cancel_requested"]),
            created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
        )


JobHandler = Callable[[JobRecord, Callable[[float, str], None], Callable[[], bool]], Awaitable[dict[str, Any]]]


class SingleWriterCommandQueue:
    """One worker serializes mutating commands and exposes cooperative cancellation."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.handlers: dict[str, JobHandler] = {}
        self._wake = asyncio.Event()
        self._stopping = False
        self._worker: asyncio.Task[None] | None = None

    def register(self, kind: str, handler: JobHandler) -> None:
        if kind in self.handlers:
            raise ValueError(f"handler already registered: {kind}")
        self.handlers[kind] = handler

    def submit(self, kind: str, payload: dict[str, Any]) -> JobRecord:
        if kind not in self.handlers:
            raise KeyError(f"unknown job kind: {kind}")
        job = self.store.create(kind, payload)
        self._wake.set()
        return job

    async def start(self) -> None:
        if self._worker is None:
            self.store.recover_interrupted()
            self._worker = asyncio.create_task(self._run(), name="miniclaude-command-writer")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._worker is not None:
            await self._worker
            self._worker = None

    async def _run(self) -> None:
        while not self._stopping:
            job = self.store.claim_next()
            if job is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            handler = self.handlers.get(job.kind)
            if handler is None:
                self.store.fail(job.job_id, f"handler unavailable after restart: {job.kind}")
                continue
            progress = lambda value, message="": self.store.update_progress(job.job_id, value, message)
            cancelled = lambda: self.store.get(job.job_id).cancel_requested
            try:
                result = await handler(job, progress, cancelled)
                if cancelled():
                    self.store.cancel_running(job.job_id)
                else:
                    self.store.finish(job.job_id, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.fail(job.job_id, str(exc))
