# Leased Compensating Restore Transactions

Stage 17B makes recovery an independent durable operation. A restore never rewrites the source change Journal from `committed` to `rolled_back`; it creates a new restore receipt that references one or more source transactions.

## State machine

```text
requested → preflighting → ready → materializing → verified → committing → completed
                 │            │            │              │
                 ├→ unavailable/corrupt    ├→ conflicted  └→ recovering → failed
                 └→ aborted                └→ aborted                    └→ conflicted
```

The implementation writes every transition atomically under `transactions/restores/journals/`. Original change receipts remain committed and can be correlated with any number of compensating restore receipts.

## Restore protocol

1. Sort source transactions newest-first by their committed state timestamp.
2. Verify every source is committed, unexpired and not finalized.
3. Verify repeated paths form a contiguous preimage/postimage chain.
4. Calculate one final target per path, so a multi-transaction rewind writes each path once.
5. Calculate the newest expected live state for every path.
6. Check all required warm/hot manifests and create a durable digest lease.
7. Check free disk for the entire target materialization before writing the workspace.
8. Authenticate all compensation dependencies and materialize every file target to restore staging.
9. Recheck the live workspace under the cross-process commit lock.
10. Enter `committing`, install each verified target, fsync and re-hash its postcondition.
11. Mark the restore completed and release its lease.

No workspace path is touched if any dependency is absent, any transaction chain is discontinuous, disk space is insufficient, or any live path conflicts. File targets are installed from verified staging rather than decrypted repeatedly during commit.

## Leases and garbage collection

The lease lists exact warm and hot digests needed for both the target and crash compensation. Warm Mark-and-Sweep and hot TTL cleanup treat leased digests as live. This prevents a six-hour cleanup tick or a 24-hour hot expiry from deleting bytes during a multi-gigabyte restore.

Completed, failed-and-compensated and pre-commit-aborted restores release leases automatically. A crash restore that sees content matching neither the target nor the pre-restore state enters `conflicted` and deliberately retains its lease. `abort_restore_transaction` is the explicit, approval-gated way to accept that live state and release the lease.

## Crash recovery

- Interruption before `committing`: mark `aborted`; no workspace compensation is needed.
- Interruption during `committing`: every live path must match either its target or expected pre-restore state. If so, restore all expected states and mark the operation recovered/aborted.
- Unknown live content: mark `conflicted`, preserve the content and lease, and require operator review.

This provides crash recoverability rather than filesystem-wide atomicity. A reader can observe intermediate files during a multi-file commit, but startup can deterministically compensate them.

## Interfaces

- `preview_restore_transactions`: no-write dependency and conflict preview.
- `restore_transactions`: one compensating restore over a contiguous transaction set.
- `rollback_change_set`: compatibility surface that delegates to a one-source restore.
- `restore_transaction_status`: list or inspect restore receipts.
- `abort_restore_transaction`: release a conflicted restore lease after approval.

## Boundaries

- External side effects, database mutations and remote APIs are outside file restore scope.
- ACLs, xattrs, sparse layout, hard-link topology and alternate streams are not restored.
- Cross-volume staging falls back to a streamed sibling temporary copy; the final replacement is same-directory atomic.
- Restore Journals are durable local metadata, not replicated disaster recovery.
