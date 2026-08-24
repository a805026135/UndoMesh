# Replay and Fork Guide

Use `SQLiteEventStore` with `ReplayEngine`. `replay(session_id, ReplayMode.DRY_RUN)` reconstructs messages without invoking a model or tool. `restore_context` resumes the projected conversation. `fork` copies events through a chosen sequence into a new session and records fork lineage.

`recorded-tools` and `recorded-model` are projection modes in v0.5; they do not silently call external services. Run `python scripts/demo_offline.py` for a record → replay → fork → provenance-denial demonstration.
