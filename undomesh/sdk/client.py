"""Python SDK that delegates to AppService instead of duplicating runtime logic."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

from undomesh.app import UndoMeshApp
from undomesh.core.config import UndoMeshConfig
from undomesh.runtime.jobs import JobRecord, JobState
from undomesh.server.service import AppService


class UndoMeshClient:
    def __init__(self, config: UndoMeshConfig, *, job_db: Path | None = None) -> None:
        self.service = AppService(UndoMeshApp(config), job_db or config.data_dir / "runtime" / "jobs.db")

    async def __aenter__(self) -> "UndoMeshClient":
        await self.service.start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.service.close()

    def submit(self, prompt: str) -> JobRecord:
        return self.service.submit_query(prompt)

    def restore(self, transaction_ids: list[str] | tuple[str, ...]) -> JobRecord:
        return self.service.submit_restore(transaction_ids)

    def rewind(self, checkpoint_id: str, mode: str = "both") -> JobRecord:
        return self.service.submit_rewind(checkpoint_id, mode)

    def audit_recovery(self, transaction_id: str) -> JobRecord:
        return self.service.submit_recovery_audit(transaction_id)

    def get(self, job_id: str) -> JobRecord:
        return self.service.jobs.get(job_id)

    def cancel(self, job_id: str) -> JobRecord:
        return self.service.cancel(job_id)

    def resume(self, job_id: str) -> JobRecord:
        return self.service.resume(job_id)

    async def stream(self, *, after: float = 0) -> AsyncIterator[dict[str, Any]]:
        async for event in self.service.events(after=after):
            yield event

    async def prompt(self, prompt: str) -> str:
        job = self.submit(prompt)
        while True:
            current = self.get(job.job_id)
            if current.state == JobState.SUCCEEDED:
                return str((current.result or {}).get("response", ""))
            if current.state in {JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}:
                raise RuntimeError(current.message or current.state.value)
            await asyncio.sleep(0.05)
