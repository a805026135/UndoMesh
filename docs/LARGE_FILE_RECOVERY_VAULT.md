# Large-file Recovery Vault

Stage 17A separates rollback storage into two physical and policy tiers. The existing 2 GiB warm store remains optimized for ordinary source files and 7–30 day retention. Files at or above 256 MiB use an independent encrypted hot vault with a default 24-hour recovery window and 50 GiB ceiling.

## Safety contract

`delete_files_transactionally` is the supported deletion path, including binary files that cannot be represented safely as a unified diff:

```text
validate exact workspace-relative files
  → write proposed Journal
  → check hot-vault capacity and disk reserve
  → stream source through CDC/compression/AES-GCM
  → atomically publish manifest and full SHA-256 proof
  → write prepared Journal with preimage and expiry
  → reacquire transaction lock and re-hash live source
  → write committing WAL state
  → unlink source and fsync parent
  → verify absence and write committed receipt
```

Failure before `committing` leaves the original file untouched. Failure after root mutation enters the existing crash-recovery path and restores every preimage. A 10 GiB incompressible file therefore needs roughly 10 GiB of physical backup space; the system never pretends that it can preserve arbitrary 10 GiB content inside the 2 GiB warm pool. If the configured hot ceiling or minimum free-disk reserve would be violated, deletion fails closed.

The admission check is deliberately conservative and reserves the logical source size before compression. This can reject a highly compressible file that might have fit physically, but prevents a late disk-full failure after spending time reading a very large source. A later implementation may use filesystem reflinks or same-volume quarantine to reduce that conservatism.

## Streaming and exact restoration

Backup input and restore output are streaming. Memory is bounded by one content-defined chunk plus I/O buffers rather than file size. The store publishes the manifest only after all chunks exist. Restore writes a sibling temporary file, authenticates and hashes every chunk, fsyncs it, atomically replaces the target, then re-reads the installed file and checks the full SHA-256 postcondition. It never asks the model to regenerate missing bytes.

`audit_transaction_recovery` is a read-only proof operation. It authenticates every required preimage and compares current workspace files with the transaction postimage. Its explicit health result is:

- `available`: all required bytes authenticate and live state is compatible;
- `conflicted`: later workspace content would be overwritten;
- `expired`: the recovery window or required object is no longer available;
- `corrupt`: a manifest, key, ciphertext, decompression bound, chunk hash or file hash failed.

`external` and `degraded` are reserved for a future Git/LFS/object-store fallback tier; the current implementation does not claim those sources exist.

## Lifecycle and permanent purge

Each hot snapshot records its own `backup_expires_at`. Startup and the configured periodic retention pass sweep expired hot references; active/recovering and pinned transactions remain protected. Once a required preimage expires, the receipt records `hot_backup_expired_at`, `recoverability=expired`, and `rollback_available=false`. Unreferenced hot manifests and chunks are unlinked directly rather than entering the warm store's extra trash day.

Early cleanup is intentionally different from ordinary retention:

1. Call `purge_large_backup_cache` with `dry_run=true` and exact transaction IDs.
2. The preview binds sorted transaction IDs, content digests and logical bytes into a SHA-256 `purge_token`.
3. Call again with `dry_run=false` and that exact token.
4. The permission layer requires fresh human confirmation and never caches it.
5. The engine recomputes the scope under the transaction lock; any mismatch rejects the purge.

Purge cannot target an active or recovering transaction. Shared chunks remain while another live hot snapshot references them. “Permanent” means UndoMesh removes its recovery references and unlinks its local objects; it is not a forensic secure erase guarantee for SSD firmware, copy-on-write snapshots, cloud synchronization, Git objects or external backup software.

## Configuration

```text
UNDOMESH_TRANSACTION_HOT_THRESHOLD_BYTES=268435456
UNDOMESH_TRANSACTION_HOT_TTL_HOURS=24
UNDOMESH_TRANSACTION_HOT_MAX_BACKUP_BYTES=53687091200
UNDOMESH_TRANSACTION_HOT_MINIMUM_FREE_BYTES=2147483648
```

The hot vault inherits the warm store's chunking and encryption settings but has separate manifests, chunks, keys and capacity metrics under `transactions/hot-vault/`. `transaction_storage_status` reports both tiers.

## Known boundaries and next stage

- The Python Gear loop processes each byte and is memory-safe but not yet optimized for maximum multi-gigabyte throughput.
- Unified-diff staging still copies the workspace; large binary deletion should use `delete_files_transactionally`.
- Recovery is verified and conflict-safe, but it still mutates the original Journal state rather than creating an independent compensating restore transaction.
- Restore leases, multi-transaction all-or-nothing preflight, key rotation/keyring custody, filesystem reflinks and external immutable sources remain future work.
- ACLs, xattrs, alternate data streams, sparse-file layout and hard-link topology are not preserved; file bytes and POSIX mode are the current contract.

Stage 17B will add restore leases and an independent restore Journal before prompt-level checkpoint rewind composes multiple change transactions.
