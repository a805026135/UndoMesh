# Production Workflow Foundation

Stages 10–12 harden the boundary around model-driven execution without claiming complete host isolation.

Shell subprocesses receive a small environment allowlist; API keys, tokens, passwords, credentials and auth variables are removed. `detect_sandbox()` reports whether only process-group controls are active or an OS backend is available. The current Windows default does not isolate filesystem or network at kernel level.

Provider secrets remain memory-only by default. Installing `miniclaude[credentials]` enables the `KeyringCredentialStore` adapter for an OS-supported credential backend; provider JSON still contains no secret.

Agent-produced unified diffs should enter through `PatchSet.parse()` and `GitPatchWorkflow`. Paths are validated before `git apply --check`; accepted patches carry a content hash and verification evidence. Verification commands run without a shell and reject pipes, redirects and chaining operators.

`apply_patch_set` exposes this workflow to the Agent through the normal ToolExecutor. Direct write/edit/shell tools are not registered in the default transactional mode. Each proposal is copied to an isolated staging workspace, verified there, and committed only if every live target still matches its prepared preimage.

Preimages and postimages are content-addressed SHA-256 blobs under `.miniclaude/transactions`; an atomically replaced journal records every state transition. The commit lock closes the conflict-check/write race between MiniClaude processes. If a process stops after entering `committing`, startup recovery restores all preimages. `rollback_change_set` restores a committed transaction only when doing so cannot overwrite newer edits, while `change_transaction_status` returns its receipt and transition history. File creation, deletion and rename are included.

This is a crash-recoverable file transaction rather than a globally atomic filesystem transaction: readers can observe a partial multi-file commit before recovery, and external side effects are outside its scope. Verification code is credential-scrubbed and staging-isolated but is not yet placed in a kernel network/filesystem sandbox. ACLs, extended attributes and hard-link identity are not preserved. See `docs/DURABLE_CHANGE_TRANSACTIONS.md`.

Rollback blobs follow reference-safe retention rather than per-transaction deletion. Committed receipts retain rollback for 30 days by default; active, recovery-conflicted and pinned receipts remain protected. Expired blobs move to trash before physical unlink, and destructive lifecycle tools require confirmation. The hard backup budget fails a new proposal before root mutation instead of silently evicting protected rollback data. See `docs/TRANSACTION_RETENTION.md`.

New backups use content-defined chunks, cross-version deduplication, conditional zlib compression and AES-256-GCM authentication. Restore is bounded and verifies chunk and whole-file hashes. A local owner-only key is generated unless an external 32-byte key is configured; key loss and whole-directory compromise remain documented limitations. See `docs/CHUNKED_BACKUP_STORE.md`.

Run `python scripts/run_offline_benchmarks.py` for the ten-suite report and `python scripts/release_check.py` before packaging. CI executes the release check on Windows/Linux and Python 3.11/3.13.
