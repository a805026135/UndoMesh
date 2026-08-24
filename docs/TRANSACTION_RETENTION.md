# Transaction Backup Retention and Garbage Collection

Durable rollback data has a bounded lifecycle. Removing a file from the workspace is not physical erasure while a protected transaction still references its preimage; this is necessary for rollback. UndoMesh separates the small audit receipt from the potentially large content-addressed blobs.

## Default lifecycle

```text
committed ──30 days──→ expired
    │                    │
    ├─ pin ──────────────┤ protected until unpinned
    └─ finalize ─────────→ eligible immediately

rejected/conflicted/rolled_back ──7 days──→ expired
active/recovering/recovery_conflicted ─────→ never auto-expire

expired blob → trash ──1 day grace──→ unlink
```

`finalize_change_transaction` means that the user accepts a committed change and no longer needs its rollback window. It does not directly delete a blob because another transaction may reference the same SHA-256. `pin_change_transaction` protects important rollback data from time-based expiry. Unpinning, finalizing and non-dry-run pruning require the normal human-confirmation path.

`prune_change_transactions` defaults to `dry_run=true`. Its report identifies eligible/protected transactions, blobs that would enter trash, trash files ready for deletion, reclaimable/released bytes, current storage and bytes above the configured budget.

## Reference-safe collection

Collection runs under the transaction cross-process lock:

1. Parse every Journal; malformed metadata fails closed.
2. Protect active, recovering, recovery-conflicted and pinned transactions.
3. Apply the committed and terminal retention windows to the remaining receipts.
4. Mark eligible receipts `recoverability=expired` before touching blobs.
5. Mark every digest referenced by a still-protected Journal.
6. Atomically move unmarked, sufficiently old blobs into `trash/`.
7. Unlink trash only after its grace period.

The orphan grace protects blobs written immediately before a process crash or before its Journal reference was persisted. Blob creation performs deduplication, quota checking and durable storage under the same cross-process lock, preventing concurrent transactions from overshooting the configured budget.

Journal metadata and hashes remain for audit after blob expiry, but `rollback_available=false` makes the loss of rollback data explicit. Journals are small and are not automatically erased in this stage. A later audit-archive policy may compact them without pretending that history never existed.

## Capacity behavior

The default hard budget is 2 GiB across compressed/encrypted chunks, manifests, legacy blobs and trash. Retention never evicts an active, conflicted, pinned or not-yet-expired rollback window merely to satisfy the budget. If the budget cannot accommodate a new unique chunk, the change transaction fails before modifying the real workspace and asks the user to finalize/prune old transactions. Trash continues counting against the budget until the grace period ends.

Configuration:

```text
UNDOMESH_TRANSACTION_COMMITTED_RETENTION_DAYS=30
UNDOMESH_TRANSACTION_TERMINAL_RETENTION_DAYS=7
UNDOMESH_TRANSACTION_TRASH_GRACE_DAYS=1
UNDOMESH_TRANSACTION_ORPHAN_GRACE_HOURS=1
UNDOMESH_TRANSACTION_MAX_BACKUP_BYTES=2147483648
UNDOMESH_TRANSACTION_AUTO_PRUNE=true
UNDOMESH_TRANSACTION_PRUNE_INTERVAL_HOURS=6
UNDOMESH_TRANSACTION_BACKUP_ENCRYPTION=true
UNDOMESH_TRANSACTION_CHUNK_MIN_BYTES=16384
UNDOMESH_TRANSACTION_CHUNK_AVG_BYTES=65536
UNDOMESH_TRANSACTION_CHUNK_MAX_BYTES=262144
```

## Security and deletion boundary

Chunks, keys and Journals use best-effort owner-only permissions and are excluded from Git. Backup chunks are compressed and AES-256-GCM encrypted by default; the locally generated fallback key remains on the same machine, so full-directory compromise and external key loss remain explicit risks. Sensitive target paths are denied, but ordinary source files may still contain confidential material; disk encryption remains advisable. Unlinking does not guarantee forensic secure erasure on SSD, copy-on-write filesystems, cloud sync, filesystem snapshots or external backup software. Git objects and other tools may independently retain deleted content.

Current storage uses Gear content-defined chunking, chunk SHA-256 deduplication, conditional zlib compression and per-chunk authenticated encryption while preserving legacy raw-blob reads. Keyring-backed rotation, compact Journal archives and filesystem-aware secure deletion remain future improvements. See `docs/CHUNKED_BACKUP_STORE.md`.

## Separate large-file hot tier

The 2 GiB budget is not a promise to retain arbitrary multi-gigabyte files. Files at or above 256 MiB are streamed into an independent hot vault with a 24-hour default TTL, 50 GiB ceiling and 2 GiB free-disk reserve. Automatic expiry directly unlinks unreferenced hot objects; active/recovering and pinned transactions remain protected. Early purge requires an exact preview token and fresh, non-cached human confirmation. See `docs/LARGE_FILE_RECOVERY_VAULT.md` for the deletion, audit, restore and permanent-purge contract.
