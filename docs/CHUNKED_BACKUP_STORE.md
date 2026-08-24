# Chunked, Compressed and Encrypted Rollback Store

Stage 16 turns the 2 GiB rollback area into a bounded content store rather than a directory of full-file copies. Existing SHA-256 file snapshots remain the transaction contract, while their physical representation becomes a manifest over reusable chunks.

## Write path

```text
file stream
  → SHA-256 file identity
  → Gear content-defined chunking (16 KiB / 64 KiB / 256 KiB)
  → SHA-256 chunk identity
  → zlib level-6 when smaller
  → AES-256-GCM with random 96-bit nonce and digest-bound AAD
  → durable chunk write
  → atomic file manifest (published last)
```

Content-defined boundaries resynchronize after insertions and deletions, unlike fixed-size blocks where a one-byte insertion can shift every later block. Identical chunks across files and transaction versions share one physical chunk. Empty files use an empty manifest. Store and restore now stream data with memory bounded by one maximum-size chunk. Restore authenticates and decompresses each bounded chunk, checks its declared size and SHA-256, verifies the whole-file size and digest while writing a sibling temporary file, atomically replaces the target, and then re-hashes the installed file as a postcondition.

The format is self-describing (`MCC1` header, compression/encryption flags, key ID and nonce). Existing raw files under `blobs/` remain readable, so the migration does not invalidate Stage 14/15 backups.

## Encryption and keys

Encryption is enabled by default. If `UNDOMESH_TRANSACTION_BACKUP_KEY` contains a 32-byte hex/base64 key, chunks use that external key. Otherwise UndoMesh generates an owner-only local AES key under `transactions/keys/`; key IDs allow existing chunks to identify the required key. AES-GCM rejects modified ciphertext, header/AAD mismatches and wrong keys.

The local-key fallback protects chunks copied without their key but does not protect against an attacker who can read the entire transaction directory. Losing an external key makes its backups unrecoverable. Keyring-backed keys, rotation/re-encryption and recovery-key export are future work. Filenames and manifests expose content hashes, sizes and equality even though file bytes are encrypted.

## Capacity and eviction

The 2 GiB limit charges compressed/encrypted chunks, manifests, legacy blobs and trash—not logical uncompressed file sizes. Blob creation, deduplication and quota checks share the cross-process transaction lock.

Eviction is safety-first:

1. Active, recovering, recovery-conflicted and pinned receipts are protected.
2. Receipts inside their declared rollback window are protected.
3. Finalized or expired receipts become eligible.
4. Eligible receipts are ordered by ascending importance, then oldest state date.
5. Mark-and-Sweep preserves manifests/chunks referenced by any protected receipt.
6. Unreachable objects enter trash before delayed unlink.

Importance is inferred conservatively from touched paths: manifests/migrations and security/payment/authentication code rank above normal source; tests, documentation and generated outputs rank lower. It only orders already-eligible cleanup and never silently shortens a promised rollback window. `pin_change_transaction` is the authoritative user override.

Startup and the six-hour periodic task perform policy cleanup. Manual `prune_change_transactions` remains dry-run by default and reports impact. If protected data fills the hard budget, a new transaction fails before root mutation rather than evicting protected recovery data.

`transaction_storage_status` reports logical bytes, unique raw chunk bytes, physical chunk bytes, deduplicated bytes, compression ratio, trash, capacity and remaining space.

## Current boundaries

- Compression is zlib rather than Zstandard to keep the runtime portable.
- Chunks are encrypted independently; manifests are integrity-checked indirectly by the final file hash but are not encrypted.
- Hash filenames reveal equality.
- Whole-directory compromise includes locally generated keys.
- Unlink is not forensic secure erasure on SSD, snapshots or synchronized storage.
- Importance is heuristic and cannot override pin/active-state safety.
- Files at or above the configured large-file threshold use the separate 24-hour hot vault described in `docs/LARGE_FILE_RECOVERY_VAULT.md`.
