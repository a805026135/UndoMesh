"""Synchronous lifecycle event dispatch for runtime observers."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from miniclaude.runtime.events import AgentEvent, EventType

logger = logging.getLogger(__name__)

EventListener = Callable[[AgentEvent], None]


class ListenerFailurePolicy(StrEnum):
    """How a listener failure affects the event publisher."""

    LOG = "log"
    RAISE = "raise"


@dataclass(frozen=True, slots=True)
class ListenerRegistration:
    listener: EventListener
    failure_policy: ListenerFailurePolicy
    name: str


class LifecycleDispatcher:
    """Dispatch immutable events to ordered runtime observers.

    This first dispatcher is deliberately synchronous: event listeners may
    observe or reject publication, but cannot race event ordering. Long-running
    or asynchronous work belongs in a later event consumer, not in the query
    loop's publication path.
    """

    def __init__(self) -> None:
        self._typed: dict[EventType, list[ListenerRegistration]] = defaultdict(list)
        self._all: list[ListenerRegistration] = []

    def subscribe(
        self,
        listener: EventListener,
        *,
        event_type: EventType | None = None,
        failure_policy: ListenerFailurePolicy = ListenerFailurePolicy.LOG,
        name: str | None = None,
    ) -> Callable[[], None]:
        """Register a listener and return an idempotent unsubscribe callback."""
        registration = ListenerRegistration(
            listener=listener,
            failure_policy=failure_policy,
            name=name or getattr(listener, "__name__", type(listener).__name__),
        )
        registrations = self._all if event_type is None else self._typed[event_type]
        registrations.append(registration)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if not active:
                return
            active = False
            registrations.remove(registration)

        return unsubscribe

    def publish(self, event: AgentEvent) -> None:
        """Publish in registration order, with typed listeners before catch-all."""
        registrations = [*self._typed.get(event.event_type, ()), *self._all]
        for registration in tuple(registrations):
            try:
                registration.listener(event)
            except Exception:
                logger.exception(
                    "Lifecycle listener '%s' failed for %s",
                    registration.name,
                    event.event_type,
                )
                if registration.failure_policy == ListenerFailurePolicy.RAISE:
                    raise
