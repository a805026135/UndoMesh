"""Runtime lifecycle primitives for UndoMesh."""

from undomesh.runtime.events import AgentEvent, EventType
from undomesh.runtime.dispatcher import LifecycleDispatcher, ListenerFailurePolicy
from undomesh.runtime.hooks import HookBus, HookContext, HookOutcome, HookPoint, HookVerdict
from undomesh.runtime.invariants import LifecycleInvariantError, validate_lifecycle
from undomesh.runtime.replay import ReplayEngine, ReplayMode, ReplayResult
from undomesh.runtime.store import EventStore, InMemoryEventStore, SQLiteEventStore

__all__ = [
    "AgentEvent",
    "EventType",
    "EventStore",
    "InMemoryEventStore",
    "SQLiteEventStore",
    "LifecycleDispatcher",
    "HookBus",
    "HookContext",
    "HookOutcome",
    "HookPoint",
    "HookVerdict",
    "LifecycleInvariantError",
    "ListenerFailurePolicy",
    "ReplayEngine",
    "ReplayMode",
    "ReplayResult",
    "validate_lifecycle",
]
