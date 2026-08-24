"""Task-local execution identity propagated into transactional tools."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
import json

from undomesh.execution.capabilities import ToolExecutionContext


_CURRENT: ContextVar[ToolExecutionContext | None] = ContextVar(
    "undomesh_tool_execution_context",
    default=None,
)


@contextmanager
def bind_tool_execution_context(context: ToolExecutionContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_tool_execution_context() -> ToolExecutionContext | None:
    return _CURRENT.get()


def current_causal_metadata() -> dict[str, object]:
    """Return a JSON-safe identity envelope for Change/Restore Journals."""
    context = current_tool_execution_context()
    if context is None:
        return {}
    metadata: dict[str, object] = {
        "session_id": context.session_id,
        "turn_id": context.turn_id,
        "step_id": context.step_id,
        "agent_id": context.agent_id,
    }
    for key in (
        "task_id",
        "parent_task_id",
        "worktree_id",
        "baseline",
    ):
        value = context.metadata.get(key)
        if value:
            metadata[key] = value
    for key in ("declared_read_paths", "declared_write_paths", "dependency_task_ids"):
        value = context.metadata.get(key)
        if not value:
            continue
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = []
        if isinstance(decoded, list):
            metadata[key] = [str(item) for item in decoded]
    return metadata
