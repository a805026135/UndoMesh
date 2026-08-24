"""Stage 7: dependency and write-set constrained scheduling."""

from __future__ import annotations

import asyncio

import pytest

from miniclaude.agents.contracts import ContractResult, TaskContract, TaskStatus
from miniclaude.agents.scheduler import ContractScheduler, ContractValidationError, paths_conflict
from miniclaude.runtime.events import EventType
from miniclaude.runtime.store import InMemoryEventStore


def test_path_conflicts_are_conservative() -> None:
    assert paths_conflict(("src",), ("src/api.py",))
    assert paths_conflict(("tests/*.py",), ("tests/unit/test_x.py",))
    assert not paths_conflict(("src/a.py",), ("tests/a.py",))


@pytest.mark.asyncio
async def test_non_conflicting_tasks_parallelize_but_conflicts_serialize(tmp_path) -> None:
    active = 0
    maximum = 0

    async def runner(contract, workspace):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ContractResult(contract.task_id, TaskStatus.COMPLETED)

    scheduler = ContractScheduler(tmp_path, runner, isolate_writes=False)
    await scheduler.execute([
        TaskContract("a", "code_analyst", "a", writable_paths=("src/a.py",)),
        TaskContract("b", "code_analyst", "b", writable_paths=("tests/b.py",)),
    ])
    assert maximum == 2

    maximum = 0
    await scheduler.execute([
        TaskContract("c", "code_analyst", "c", writable_paths=("src",)),
        TaskContract("d", "code_analyst", "d", writable_paths=("src/d.py",)),
    ])
    assert maximum == 1


@pytest.mark.asyncio
async def test_dependencies_budgets_and_events(tmp_path) -> None:
    store = InMemoryEventStore()

    async def runner(contract, workspace):
        if contract.task_id == "root":
            return ContractResult(contract.task_id, TaskStatus.FAILED, error="boom")
        return ContractResult(contract.task_id, TaskStatus.COMPLETED, tokens=11)

    scheduler = ContractScheduler(
        tmp_path, runner, isolate_writes=False, event_store=store, session_id="agents"
    )
    results = await scheduler.execute([
        TaskContract("root", "code_analyst", "root"),
        TaskContract("child", "code_analyst", "child", dependencies=("root",)),
        TaskContract("budget", "code_analyst", "budget", token_budget=10),
    ])

    assert results["child"].status == TaskStatus.SKIPPED
    assert results["budget"].status == TaskStatus.FAILED
    events = store.list_events("agents")
    assert [event.event_type for event in events].count(EventType.AGENT_SPAWNED) == 2
    assert any(event.payload.get("status") == "skipped" for event in events)


@pytest.mark.asyncio
async def test_cycle_and_deadline_are_enforced(tmp_path) -> None:
    async def slow_runner(contract, workspace):
        await asyncio.sleep(0.05)
        return ContractResult(contract.task_id, TaskStatus.COMPLETED)

    scheduler = ContractScheduler(tmp_path, slow_runner, isolate_writes=False)
    with pytest.raises(ContractValidationError, match="cycle"):
        await scheduler.execute([
            TaskContract("a", "code_analyst", "a", dependencies=("b",)),
            TaskContract("b", "code_analyst", "b", dependencies=("a",)),
        ])
    results = await scheduler.execute([
        TaskContract("slow", "code_analyst", "slow", deadline_seconds=0.001)
    ])
    assert results["slow"].status == TaskStatus.FAILED
    assert results["slow"].error == "Deadline exceeded"


@pytest.mark.asyncio
async def test_parent_cancellation_reaches_running_child(tmp_path) -> None:
    store = InMemoryEventStore()

    async def blocked_runner(contract, workspace):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    scheduler = ContractScheduler(
        tmp_path, blocked_runner, isolate_writes=False, event_store=store, session_id="cancel"
    )
    parent = asyncio.create_task(
        scheduler.execute([TaskContract("child", "code_analyst", "wait")])
    )
    for _ in range(10):
        if store.list_events("cancel"):
            break
        await asyncio.sleep(0)
    assert store.list_events("cancel")[0].event_type == EventType.AGENT_SPAWNED
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert EventType.AGENT_CANCELLED in [
        event.event_type for event in store.list_events("cancel")
    ]
