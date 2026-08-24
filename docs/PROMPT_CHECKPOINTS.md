# Prompt Checkpoints and Safe Rewind

Stage 18 derives one checkpoint from every immutable `TURN_STARTED` event. Checkpoints do not duplicate workspace snapshots: they reference the successful change transaction IDs recorded by later `TOOL_COMPLETED` events.

## Checkpoint health

For each prompt boundary, UndoMesh computes the newest-to-oldest transaction closure after that event and previews one compensating restore. Live file hashing is cached per path during a checkpoint listing, avoiding repeated multi-gigabyte reads when many prompts touch the same file.

The UI reports:

- `available`: dependencies exist and the live workspace matches the newest postimages;
- `conflicted`: a rewind would overwrite later unknown content;
- `unavailable`: a source receipt is finalized/expired, a dependency is missing, or the transaction chain is discontinuous.

Actual code rewind performs stronger full chunk authentication and materialization before its first workspace write.

## Four modes

### Code

Restore every successful change transaction after the selected prompt through one leased restore transaction. The current conversation continues and receives append-only rewind audit events.

### Conversation

Fork the session through the selected `TURN_STARTED` sequence. The source session remains unchanged. The new session contains copied immutable events with `replayed_from_event_id`, a `SESSION_FORKED` fact and a `SESSION_REWOUND` fact. Workspace code is untouched.

### Both

Preflight and restore code first, then fork the conversation at the prompt boundary. Both source and active sessions receive completion facts so a requested rewind never appears permanently unfinished in the audit log.

### Summary

Append a `CONTEXT_COMPACTED` projection that shadows later model-visible events and inserts a deterministic bounded summary. Raw events are not deleted. This is a context operation, not a code restore.

## User surfaces

REPL commands:

```text
/checkpoints
/rewind <index|checkpoint-id|latest> [code|conversation|both|summary]
/restores
/abort-restore <restore-id>
```

Non-interactive CLI:

```text
undomesh --checkpoints
undomesh --rewind latest --rewind-mode conversation
undomesh --rewind <id> --rewind-mode both --yes
```

Code/both tool rewinds require a fresh, non-cached confirmation. Non-interactive code rewind fails unless `--yes` is present. The desktop Recovery Center displays prompt health, transaction counts, conflicts, unavailable dependencies and recent restore receipts before offering all four actions.

`both` coordinates two durable domains but does not claim distributed atomicity. Code recovery commits through a Restore Journal before the event store creates the conversation fork. If that fork then fails, `REWIND_FAILED` records the completed `restore_id` and `code_restore_completed=true`; the code result remains independently inspectable and recoverable instead of being hidden behind a generic failure.

## Comparison boundary

The implementation follows the publicly documented checkpoint idea but is not a clone of private Claude Code or Codex internals. UndoMesh's differentiator is the explicit transaction closure, backup health, lease and compensating restore receipt. See `docs/CLAUDE_REWIND_COMPARISON.md` for source boundaries.
