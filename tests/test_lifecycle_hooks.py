from __future__ import annotations

from pathlib import Path

import pytest

from undomesh.core.tool_executor import ToolExecutor
from undomesh.execution.capabilities import CapabilitySet, ToolExecutionContext
from undomesh.runtime.events import EventType
from undomesh.runtime.hooks import (
    HookBus,
    HookContext,
    HookOutcome,
    HookPoint,
    HookVerdict,
)
from undomesh.runtime.store import InMemoryEventStore
from undomesh.tools.base import BaseTool
from undomesh.tools.registry import ToolRegistry


class TouchTool(BaseTool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "touch"

    @property
    def description(self) -> str:
        return "A hook-test tool"

    @property
    def parameters(self) -> dict:
        return {}

    async def execute(self, **kwargs):
        self.calls += 1
        return self._success("ok")


@pytest.mark.asyncio
async def test_hook_verdicts_are_monotonic_and_priority_ordered() -> None:
    bus = HookBus()
    observed: list[str] = []

    def allow(_context):
        observed.append("allow")
        return HookOutcome(HookVerdict.ALLOW, "ordinary allow")

    def deny(_context):
        observed.append("deny")
        return HookOutcome(HookVerdict.DENY, "policy denial")

    bus.register(allow, point=HookPoint.PRE_TOOL, name="allow", priority=1)
    bus.register(deny, point=HookPoint.PRE_TOOL, name="deny", priority=10)
    outcome = await bus.run(HookContext(
        point=HookPoint.PRE_TOOL, session_id="s", tool_name="write_file"
    ))

    assert observed == ["deny", "allow"]
    assert outcome.verdict == HookVerdict.DENY
    assert "policy denial" in outcome.reason
    assert "ordinary allow" in outcome.reason


@pytest.mark.asyncio
async def test_pre_tool_hook_failure_is_fail_closed() -> None:
    bus = HookBus()

    def broken(_context):
        raise RuntimeError("broken hook")

    bus.register(broken, point=HookPoint.PRE_TOOL, name="broken")
    outcome = await bus.run(HookContext(
        point=HookPoint.PRE_TOOL, session_id="s", tool_name="touch"
    ))

    assert outcome.verdict == HookVerdict.DENY
    assert "failed closed" in outcome.reason


@pytest.mark.asyncio
async def test_executor_hook_denial_has_no_side_effect_and_is_audited(config, tmp_project: Path) -> None:
    store = InMemoryEventStore()
    bus = HookBus(store)
    tool = TouchTool()
    registry = ToolRegistry(config)
    registry.register(tool)
    capabilities = CapabilitySet.for_project(tmp_project, {"touch"})
    executor = ToolExecutor(
        registry, config, default_capabilities=capabilities, hook_bus=bus
    )

    bus.register(
        lambda _context: HookOutcome(HookVerdict.DENY, "repository policy"),
        point=HookPoint.PRE_TOOL,
        matcher="touch",
        name="repo-policy",
    )
    result = await executor.execute_single(
        "touch", {}, ToolExecutionContext(
            session_id="session", turn_id="turn", step_id="step",
            capability_set=capabilities,
        )
    )

    assert not result.success
    assert tool.calls == 0
    assert [event.event_type for event in store.list_events("session")] == [
        EventType.HOOK_INVOKED,
        EventType.HOOK_COMPLETED,
    ]


def test_runtime_event_hooks_are_observe_only() -> None:
    store = InMemoryEventStore()
    event = store.append(session_id="s", event_type=EventType.SESSION_STARTED)
    bus = HookBus(store)
    observed: list[str] = []
    bus.register(
        lambda context: observed.append(context.event.event_type.value),
        point=HookPoint.RUNTIME_EVENT,
        matcher="session.*",
    )

    bus.observe_event(event)

    assert observed == ["session.started"]
