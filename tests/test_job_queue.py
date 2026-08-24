import asyncio

import pytest
from dataclasses import dataclass

from miniclaude.runtime.jobs import JobState, JobStore, SingleWriterCommandQueue
from miniclaude.server.service import AppService
from miniclaude.observability.telemetry import Telemetry, TelemetryConfig


def test_job_store_restart_and_explicit_resume(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.create("work", {"value": 1})
    assert store.claim_next().job_id == job.job_id
    assert store.recover_interrupted() == 1
    assert store.get(job.job_id).state == JobState.INTERRUPTED
    assert store.resume(job.job_id).state == JobState.QUEUED
    store.close()


def test_queued_job_can_be_cancelled_without_execution(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.create("work", {})
    assert store.request_cancel(job.job_id).state == JobState.CANCELLED
    assert store.claim_next() is None
    store.close()


@pytest.mark.asyncio
async def test_queue_is_single_writer_and_reports_progress(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    queue = SingleWriterCommandQueue(store)
    active = 0
    peak = 0

    async def handler(job, progress, cancelled):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        progress(0.5, "half")
        await asyncio.sleep(0.01)
        active -= 1
        return {"id": job.job_id}

    queue.register("work", handler)
    first = queue.submit("work", {})
    second = queue.submit("work", {})
    await queue.start()
    for _ in range(100):
        if store.get(second.job_id).state == JobState.SUCCEEDED:
            break
        await asyncio.sleep(0.005)
    await queue.stop()
    assert peak == 1
    assert store.get(first.job_id).state == JobState.SUCCEEDED
    assert store.get(second.job_id).state == JobState.SUCCEEDED
    store.close()


@pytest.mark.asyncio
async def test_app_service_streams_durable_query_completion(tmp_path):
    class FakeApp:
        async def initialize(self): pass
        async def shutdown(self): pass
        async def run_query(self, prompt):
            await asyncio.sleep(0.01)
            return prompt.upper()

    service = AppService(FakeApp(), tmp_path / "service.db")
    await service.start()
    job = service.submit_query("hello")
    observed = []
    async for event in service.events():
        if event["job_id"] == job.job_id:
            observed.append(event)
            if event["state"] == "succeeded":
                break
    assert observed[-1]["result"] == {"response": "HELLO"}
    await service.close()


@pytest.mark.asyncio
async def test_app_service_queues_restore_and_emits_redacted_telemetry(tmp_path):
    @dataclass
    class Receipt:
        restore_id: str
        state: str

    class Engine:
        def restore_transactions(self, transaction_ids):
            assert transaction_ids == ("tx-1",)
            return Receipt("restore-1", "committed")

    class FakeApp:
        change_engine = Engine()
        checkpoint_manager = None
        async def initialize(self): pass
        async def shutdown(self): pass

    telemetry = Telemetry(TelemetryConfig(enabled=True))
    service = AppService(FakeApp(), tmp_path / "restore.db", telemetry=telemetry)
    await service.start()
    job = service.submit_restore(["tx-1"])
    for _ in range(100):
        current = service.jobs.get(job.job_id)
        if current.state == JobState.SUCCEEDED:
            break
        await asyncio.sleep(0.005)
    assert current.result == {"restore_id": "restore-1", "state": "committed"}
    assert telemetry.records[0]["name"] == "job.restore"
    await service.close()
