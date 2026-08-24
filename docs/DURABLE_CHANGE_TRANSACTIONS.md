# Durable Change Transactions

Agent edits are treated as transactions, not direct filesystem writes. The guarantee is: a verified change set is committed only if every touched path still matches its prepared preimage; interrupted commits are recoverable from durable content-addressed backups.

## State machine

```text
proposed → prepared → staged → verified → committing → committed
                    ↘ rejected/rolled_back
committing + crash → recovering → rolled_back
committing + unknown live edit → recovery_conflicted
committed + explicit rollback → rolling_back → rolled_back
```

Before staging, every old and new patch path receives a `PathSnapshot`: absent, regular file with SHA-256 blob and mode, or symbolic link with target. Journal updates use write-temp plus `os.replace`. Staging occurs in an isolated workspace copy, so tests and generated files cannot affect the main workspace.

Before commit, optimistic concurrency compares every live path with its preimage. If a user or another Agent changed any target, the transaction becomes `conflicted` and does not overwrite it. A cross-process commit lock makes the comparison and writes one serialized critical section. The journal enters `committing` before the first root mutation. Regular files and symlinks are written to sibling temporary paths and atomically replaced; deletions happen only after their backup is durable. Parent symlinks and invalid transaction IDs are rejected to prevent workspace escape.

On startup, incomplete `committing` or `rolling_back` journals restore all preimages only when each live path matches its recorded preimage or postimage. A third, unknown value becomes `recovery_conflicted` and is preserved for manual resolution. Prepared/staged/verified transactions are marked rejected without root recovery because they have not mutated the root. Explicit rollback of a committed transaction is allowed only while every touched path still matches the recorded postimage. Backup content is hash-checked before restore, and the transaction receipt exposes timestamps, verification evidence, conflicts and the complete state transition history.

## User-visible contract

- `apply_patch_set`: returns a transaction receipt after isolated verification and commit.
- `rollback_change_set`: reverses a committed receipt without overwriting later user work.
- `change_transaction_status`: retrieves one receipt or recent receipts for audit/recovery UI.
- Application startup calls recovery before accepting new work.

If transaction B edits a path after transaction A, A cannot be rolled back directly. Roll back B first, then A. This deliberately favors preservation of newer work over forced historical restoration.

Rollback blobs are not retained forever. Their state-aware retention, explicit finalize/pin operations, reference-safe garbage collection, trash grace and capacity budget are specified in `docs/TRANSACTION_RETENTION.md`. Expiry removes rollback availability but retains a small auditable receipt and content hashes.

## Guarantee boundary

The transaction covers regular files, new files, deletions, renames, text/binary patches, executable mode where supported, and symlinks where the host permits them. It does not roll back network requests, databases, package-manager side effects, processes spawned outside the isolated verifier, filesystem ACLs, extended attributes, hard-link identity, empty directory creation, or changes outside declared patch paths. Multi-file replacement is crash-recoverable but not globally atomic: another process may briefly observe a partial commit. Verification commands remain allowlisted, credential-scrubbed and staging-local, but are not an OS security sandbox.
