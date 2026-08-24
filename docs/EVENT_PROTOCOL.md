# Event Protocol

An `AgentEvent` has immutable ID, session ID, optional turn/step IDs, monotonic session sequence, timestamp, type, payload and optional provenance. SQLite schema migration is versioned; secret-like payload keys are redacted before persistence.

Lifecycle terminals are explicit: turns end, fail or cancel; steps end or fail; requested tool calls complete, fail or are denied. Context compaction appends start/result events containing the shadowed event IDs and summary. It never deletes source events.

Replay projects recorded facts and has no side effects by default. Consumers should use event types rather than parsing log strings and must tolerate new event types.
