# Claude Code Rewind Comparison

This comparison is based on Anthropic's public documentation, not private Claude Code source code.

## Public Claude Code behavior

Anthropic documents that Claude Code snapshots affected files before edits and creates a checkpoint for each user prompt. Checkpoints persist with sessions and are cleaned up with session retention, documented as 30 days by default. `/rewind` or double Escape can restore code, conversation, both, or summarize from/to a selected prompt. The Agent SDK exposes checkpoint UUIDs and `rewind_files()`/`rewindFiles()` for file-only restore.

The documented boundary is important: Write, Edit and NotebookEdit are tracked; Bash mutations and unrelated external edits are not. Checkpoints are local session undo and do not replace Git. Conversation rewind truncates to an earlier prompt prefix, while forking preserves the original session.

Sources: [Claude Code checkpointing](https://code.claude.com/docs/en/checkpointing), [Agent SDK file checkpointing](https://code.claude.com/docs/en/agent-sdk/file-checkpointing), [session storage and retention](https://code.claude.com/docs/en/sessions), [prompt-cache behavior during rewind](https://code.claude.com/docs/en/prompt-caching).

## MiniClaude mapping

MiniClaude already has stronger file-level primitives in several areas:

- every default mutation uses a declared ChangeSet instead of direct edit tools;
- pre/postimages cover creation, deletion and rename;
- isolated verification precedes root mutation;
- optimistic conflict checks preserve newer user edits;
- durable Journals recover interrupted multi-file commits;
- compressed/encrypted content-defined chunks bound storage growth;
- transaction IDs are now emitted with successful tool runtime events.

The next rewind layer should use `TURN_STARTED` as the prompt checkpoint and collect later `TOOL_COMPLETED.transaction_id` values. “Restore code” rolls those transactions back in reverse order. “Restore conversation” appends a projection marker or creates a fork rather than deleting append-only events. “Restore both” combines those operations only after a complete conflict preflight. “Summarize” remains an event projection operation and does not mutate files.

This preserves the useful Claude UX while improving its documented Bash/external-change boundary: MiniClaude refuses default direct-write/Shell bypasses and stops rewind on unknown newer content. It should still describe checkpoints as local recovery, not version control.

## Required next-stage invariants

1. A checkpoint references session ID, turn/event sequence and an ordered transaction list.
2. Multi-transaction rewind performs a full preflight, then rolls back newest-to-oldest.
3. Any conflict aborts before the first file mutation unless the user explicitly chooses a partial recovery workflow.
4. Conversation history stays append-only; rewind changes projection/fork lineage.
5. A code-only rewind never silently rewrites conversation history.
6. A conversation-only rewind never changes files.
7. Checkpoint retention cannot outlive the required transaction chunks unless marked code-unavailable.
8. Rewind receipts record which transactions, files and conversation range changed.
