"""Dependency and write-set aware scheduling for sub-agent contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePath
from typing import Any

from undomesh.agents.contracts import ContractResult, TaskContract, TaskStatus
from undomesh.agents.worktree import Worktree
from undomesh.runtime.events import EventType
from undomesh.runtime.store import EventStore

ContractRunner = Callable[[TaskContract, Path], Awaitable[ContractResult]]


class ContractValidationError(ValueError):
    pass


def paths_conflict(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Conservatively detect equal, nested, or wildcard-overlapping write paths."""
    for raw_left in left:
        for raw_right in right:
            a = raw_left.replace("\\", "/").rstrip("/")
            b = raw_right.replace("\\", "/").rstrip("/")
            if any(char in a + b for char in "*?["):
                prefix_a = a.split("*")[0].split("?")[0].rstrip("/")
                prefix_b = b.split("*")[0].split("?")[0].rstrip("/")
                if prefix_a.startswith(prefix_b) or prefix_b.startswith(prefix_a):
                    return True
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                return True
    return False


class ContractScheduler:
    def __init__(
        self,
        project_root: Path,
        runner: ContractRunner,
        *,
        max_concurrency: int = 4,
        isolate_writes: bool = True,
        event_store: EventStore | None = None,
        session_id: str = "scheduler",
    ) -> None:
        self.project_root = project_root.resolve()
        self.runner = runner
        self.max_concurrency = max(1, max_concurrency)
        self.isolate_writes = isolate_writes
        self.event_store = event_store
        self.session_id = session_id

    async def execute(self, contracts: list[TaskContract]) -> dict[str, ContractResult]:
        self._validate(contracts)
        pending = {contract.task_id: contract for contract in contracts}
        results: dict[str, ContractResult] = {}

        while pending:
            for task_id, contract in list(pending.items()):
                failed_dependencies = [
                    dependency
                    for dependency in contract.dependencies
                    if dependency in results and results[dependency].status != TaskStatus.COMPLETED
                ]
                if failed_dependencies:
                    results[task_id] = ContractResult(
                        task_id,
                        TaskStatus.SKIPPED,
                        error=f"Failed dependencies: {failed_dependencies}",
                    )
                    self._emit(
                        EventType.AGENT_COMPLETED,
                        contract,
                        {"status": TaskStatus.SKIPPED.value, "failed_dependencies": failed_dependencies},
                    )
                    del pending[task_id]

            ready = [
                contract
                for contract in pending.values()
                if all(dependency in results for dependency in contract.dependencies)
            ]
            if not ready:
                if pending:
                    raise ContractValidationError("Dependency graph made no progress")
                break

            wave: list[TaskContract] = []
            for contract in ready:
                if len(wave) >= self.max_concurrency:
                    break
                if all(
                    not paths_conflict(contract.writable_paths, selected.writable_paths)
                    for selected in wave
                ):
                    wave.append(contract)
            if not wave:
                wave = [ready[0]]

            wave_results = await asyncio.gather(
                *(self._run_one(contract) for contract in wave),
                return_exceptions=True,
            )
            for contract, outcome in zip(wave, wave_results):
                if isinstance(outcome, asyncio.CancelledError):
                    result = ContractResult(contract.task_id, TaskStatus.CANCELLED, error="Cancelled")
                elif isinstance(outcome, BaseException):
                    result = ContractResult(
                        contract.task_id,
                        TaskStatus.FAILED,
                        error=f"{type(outcome).__name__}: {outcome}",
                    )
                else:
                    result = outcome
                results[contract.task_id] = result
                del pending[contract.task_id]
        return results

    async def _run_one(self, contract: TaskContract) -> ContractResult:
        self._emit(EventType.AGENT_SPAWNED, contract, {"status": TaskStatus.RUNNING.value})
        worktree: Worktree | None = None
        workspace = self.project_root
        try:
            if self.isolate_writes and contract.writable_paths:
                worktree = Worktree(
                    self.project_root,
                    name=f"contract_{contract.task_id}",
                    copy_mode=True,
                )
                workspace = worktree.create()
            result = await asyncio.wait_for(
                self.runner(contract, workspace),
                timeout=contract.deadline_seconds,
            )
            if result.tokens > contract.token_budget:
                result = ContractResult(
                    contract.task_id,
                    TaskStatus.FAILED,
                    output=result.output,
                    error=f"Token budget exceeded: {result.tokens}>{contract.token_budget}",
                    iterations=result.iterations,
                    tokens=result.tokens,
                    workspace=str(workspace),
                )
            self._emit(EventType.AGENT_COMPLETED, contract, {"status": result.status.value})
            return result
        except asyncio.CancelledError:
            self._emit(EventType.AGENT_CANCELLED, contract, {"reason": "cancelled"})
            raise
        except asyncio.TimeoutError:
            result = ContractResult(contract.task_id, TaskStatus.FAILED, error="Deadline exceeded")
            self._emit(EventType.AGENT_COMPLETED, contract, {"status": result.status.value})
            return result
        finally:
            if worktree:
                worktree.cleanup()

    def _validate(self, contracts: list[TaskContract]) -> None:
        ids = [contract.task_id for contract in contracts]
        if len(ids) != len(set(ids)):
            raise ContractValidationError("Task IDs must be unique")
        known = set(ids)
        for contract in contracts:
            missing = set(contract.dependencies) - known
            if missing:
                raise ContractValidationError(
                    f"Task {contract.task_id} has unknown dependencies: {sorted(missing)}"
                )
        visiting: set[str] = set()
        visited: set[str] = set()
        mapping = {contract.task_id: contract for contract in contracts}

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ContractValidationError("Dependency cycle detected")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in mapping[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)

    def _emit(self, event_type: EventType, contract: TaskContract, payload: dict[str, Any]) -> None:
        if self.event_store:
            self.event_store.append(
                session_id=self.session_id,
                event_type=event_type,
                payload={"task_id": contract.task_id, "agent_type": contract.agent_type, **payload},
            )
