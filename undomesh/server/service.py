"""Transport-neutral application service used by desktop, RPC and SDK clients."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator

from undomesh.runtime.jobs import JobRecord, JobState, JobStore, SingleWriterCommandQueue
from undomesh.observability.telemetry import Telemetry, TelemetryConfig


class AppService:
    def __init__(self, app: Any, database: Path, *, telemetry: Telemetry | None = None) -> None:
        self.app = app
        enabled = bool(getattr(getattr(app, "config", None), "telemetry_enabled", False))
        self.telemetry = telemetry or Telemetry(TelemetryConfig(enabled=enabled))
        self.jobs = JobStore(database)
        self.queue = SingleWriterCommandQueue(self.jobs)
        self.queue.register("query", self._query)
        self.queue.register("restore_transactions", self._restore_transactions)
        self.queue.register("rewind_checkpoint", self._rewind_checkpoint)
        self.queue.register("audit_recovery", self._audit_recovery)

    async def start(self) -> None:
        await self.app.initialize()
        await self.queue.start()

    async def close(self) -> None:
        await self.queue.stop()
        await self.app.shutdown()
        self.jobs.close()

    def submit_query(self, prompt: str) -> JobRecord:
        return self.queue.submit("query", {"prompt": prompt})

    def submit_restore(self, transaction_ids: list[str] | tuple[str, ...]) -> JobRecord:
        if not transaction_ids:
            raise ValueError("at least one transaction is required")
        return self.queue.submit("restore_transactions", {"transaction_ids": list(transaction_ids)})

    def submit_rewind(self, checkpoint_id: str, mode: str = "both") -> JobRecord:
        return self.queue.submit("rewind_checkpoint", {"checkpoint_id": checkpoint_id, "mode": mode})

    def submit_recovery_audit(self, transaction_id: str) -> JobRecord:
        return self.queue.submit("audit_recovery", {"transaction_id": transaction_id})

    def cancel(self, job_id: str) -> JobRecord:
        return self.jobs.request_cancel(job_id)

    def resume(self, job_id: str) -> JobRecord:
        job = self.jobs.resume(job_id)
        self.queue._wake.set()
        return job

    async def events(self, *, after: float = 0, poll_seconds: float = 0.1) -> AsyncIterator[dict[str, Any]]:
        cursor = after
        terminal = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}
        while True:
            changed = self.jobs.list(after=cursor)
            for job in changed:
                cursor = max(cursor, job.updated_at)
                payload = asdict(job)
                payload["state"] = job.state.value
                yield payload
            if changed and changed[-1].state in terminal:
                return
            await asyncio.sleep(poll_seconds)

    async def _query(self, job: JobRecord, progress, cancelled) -> dict[str, Any]:
        if cancelled():
            return {}
        progress(0.05, "query accepted")
        with self.telemetry.span("job.query", job_id=job.job_id, prompt="[local]"):
            response = await self.app.run_query(str(job.payload["prompt"]))
        progress(1.0, "response ready")
        return {"response": response}

    async def _restore_transactions(self, job: JobRecord, progress, cancelled) -> dict[str, Any]:
        if cancelled():
            return {}
        progress(0.1, "validating restore dependencies")
        transaction_ids = tuple(str(item) for item in job.payload["transaction_ids"])
        with self.telemetry.span("job.restore", job_id=job.job_id, transaction_count=len(transaction_ids)):
            # The recovery coordinator performs complete preflight before its first
            # write. Cancellation is cooperative until that atomic commit boundary.
            receipt = await asyncio.to_thread(self.app.change_engine.restore_transactions, transaction_ids)
        progress(1.0, "restore committed and verified")
        return _jsonable(receipt)

    async def _rewind_checkpoint(self, job: JobRecord, progress, cancelled) -> dict[str, Any]:
        if cancelled():
            return {}
        from undomesh.runtime.checkpoints import RewindMode
        progress(0.1, "resolving checkpoint closure")
        with self.telemetry.span("job.rewind", job_id=job.job_id, mode=job.payload["mode"]):
            receipt = await asyncio.to_thread(
                self.app.checkpoint_manager.rewind,
                str(job.payload["checkpoint_id"]), RewindMode(str(job.payload["mode"])),
            )
        progress(1.0, "rewind completed")
        return _jsonable(receipt)

    async def _audit_recovery(self, job: JobRecord, progress, cancelled) -> dict[str, Any]:
        if cancelled():
            return {}
        progress(0.2, "authenticating recovery dependencies")
        report = await asyncio.to_thread(
            self.app.change_engine.audit_recovery, str(job.payload["transaction_id"])
        )
        progress(1.0, "recovery audit completed")
        return _jsonable(report)


def _jsonable(value: Any) -> dict[str, Any]:
    raw = asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)
    return {
        key: (
            item.value if hasattr(item, "value")
            else [entry.value if hasattr(entry, "value") else entry for entry in item]
            if isinstance(item, (list, tuple))
            else str(item) if isinstance(item, Path)
            else item
        ) for key, item in raw.items()
    }
