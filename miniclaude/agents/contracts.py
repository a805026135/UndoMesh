"""Explicit task and result contracts for constrained sub-agent work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TaskContract:
    task_id: str
    agent_type: str
    objective: str
    dependencies: tuple[str, ...] = ()
    readable_paths: tuple[str, ...] = ()
    writable_paths: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    token_budget: int = 20_000
    deadline_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class ContractResult:
    task_id: str
    status: TaskStatus
    output: str = ""
    error: str | None = None
    iterations: int = 0
    tokens: int = 0
    workspace: str | None = None
