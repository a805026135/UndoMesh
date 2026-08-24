"""Deterministic, monotonic lifecycle hooks for the execution boundary."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any
from uuid import uuid4

from miniclaude.runtime.events import AgentEvent, EventType
from miniclaude.runtime.store import EventStore

logger = logging.getLogger(__name__)


class HookPoint(StrEnum):
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    TOOL_FAILURE = "tool_failure"
    RUNTIME_EVENT = "runtime_event"


class HookVerdict(StrEnum):
    ABSTAIN = "abstain"
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class HookContext:
    point: HookPoint
    session_id: str
    turn_id: str | None = None
    step_id: str | None = None
    agent_id: str = "root"
    tool_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    event: AgentEvent | None = None


@dataclass(frozen=True, slots=True)
class HookOutcome:
    verdict: HookVerdict = HookVerdict.ABSTAIN
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


HookCallback = Callable[
    [HookContext], HookOutcome | None | Awaitable[HookOutcome | None]
]


@dataclass(frozen=True, slots=True)
class HookRegistration:
    name: str
    point: HookPoint
    callback: HookCallback
    matcher: str = "*"
    priority: int = 0
    fail_closed: bool = True


class HookBus:
    """Run hooks in stable order without allowing an allow to undo a deny."""

    _STRENGTH = {
        HookVerdict.ABSTAIN: 0,
        HookVerdict.ALLOW: 1,
        HookVerdict.ASK: 2,
        HookVerdict.DENY: 3,
    }

    def __init__(self, event_store: EventStore | None = None) -> None:
        self.event_store = event_store
        self._registrations: list[HookRegistration] = []

    def register(
        self,
        callback: HookCallback,
        *,
        point: HookPoint,
        name: str | None = None,
        matcher: str = "*",
        priority: int = 0,
        fail_closed: bool = True,
    ) -> Callable[[], None]:
        registration = HookRegistration(
            name=name or getattr(callback, "__name__", type(callback).__name__),
            point=point,
            callback=callback,
            matcher=matcher,
            priority=priority,
            fail_closed=fail_closed,
        )
        self._registrations.append(registration)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if active:
                active = False
                self._registrations.remove(registration)

        return unsubscribe

    async def run(self, context: HookContext) -> HookOutcome:
        selected = self._select(context)
        aggregate = HookOutcome()
        reasons: list[str] = []
        for registration in selected:
            hook_id = str(uuid4())
            self._audit(EventType.HOOK_INVOKED, context, registration, hook_id)
            try:
                value = registration.callback(context)
                if inspect.isawaitable(value):
                    value = await value
                outcome = value or HookOutcome()
                if not isinstance(outcome, HookOutcome):
                    raise TypeError("Hook callback must return HookOutcome or None")
                self._audit(
                    EventType.HOOK_COMPLETED,
                    context,
                    registration,
                    hook_id,
                    {"verdict": outcome.verdict.value, "reason": outcome.reason},
                )
            except Exception as exc:
                logger.exception("Hook '%s' failed", registration.name)
                self._audit(
                    EventType.HOOK_FAILED,
                    context,
                    registration,
                    hook_id,
                    {"error_type": type(exc).__name__, "message": str(exc)},
                )
                outcome = HookOutcome(
                    HookVerdict.DENY,
                    f"Hook {registration.name} failed closed: {type(exc).__name__}",
                ) if context.point == HookPoint.PRE_TOOL and registration.fail_closed else HookOutcome()
            if outcome.reason:
                reasons.append(f"{registration.name}: {outcome.reason}")
            if self._STRENGTH[outcome.verdict] > self._STRENGTH[aggregate.verdict]:
                aggregate = outcome
        if reasons:
            aggregate = HookOutcome(
                verdict=aggregate.verdict,
                reason="; ".join(reasons),
                metadata=aggregate.metadata,
            )
        return aggregate

    def observe_event(self, event: AgentEvent) -> None:
        """Run synchronous event observers in dispatcher order.

        Runtime observers cannot block a fact that has already been appended.
        Use PRE_TOOL for enforceable policy.
        """
        context = HookContext(
            point=HookPoint.RUNTIME_EVENT,
            session_id=event.session_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            event=event,
        )
        for registration in self._select(context):
            try:
                value = registration.callback(context)
                if inspect.isawaitable(value):
                    if inspect.iscoroutine(value):
                        value.close()
                    raise TypeError("Runtime event observers must be synchronous")
            except Exception:
                logger.exception("Runtime event hook '%s' failed", registration.name)

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": item.name,
                "point": item.point.value,
                "matcher": item.matcher,
                "priority": item.priority,
                "fail_closed": item.fail_closed,
            }
            for item in sorted(self._registrations, key=self._sort_key)
        ]

    def _select(self, context: HookContext) -> list[HookRegistration]:
        target = (
            context.event.event_type.value
            if context.point == HookPoint.RUNTIME_EVENT and context.event is not None
            else context.tool_name
        )
        return sorted(
            [
                item for item in self._registrations
                if item.point == context.point and fnmatchcase(target, item.matcher)
            ],
            key=self._sort_key,
        )

    @staticmethod
    def _sort_key(item: HookRegistration) -> tuple[int, str]:
        return (-item.priority, item.name)

    def _audit(
        self,
        event_type: EventType,
        context: HookContext,
        registration: HookRegistration,
        hook_id: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.event_store is None or context.session_id == "unknown":
            return
        self.event_store.append(
            session_id=context.session_id,
            event_type=event_type,
            turn_id=context.turn_id,
            step_id=context.step_id,
            payload={
                "hook_id": hook_id,
                "hook_name": registration.name,
                "point": context.point.value,
                "matcher": registration.matcher,
                "tool_name": context.tool_name,
                **(extra or {}),
            },
        )
