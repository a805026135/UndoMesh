# UndoMesh System Audit

Audit date: 2026-08-22

## Completion status

The planned implementation through Stage 18 is complete. UndoMesh now has a replayable event runtime, transactional file mutation, tiered encrypted recovery storage, independent compensating restores, prompt checkpoints, four rewind modes, multi-provider model configuration, controlled sub-agents, a Rich CLI and a Tk desktop client.

Release evidence:

- 169 tests passed and 1 platform-dependent test skipped;
- 13/13 fixed offline benchmark suites passed;
- `scripts/release_check.py` returned `ready: true`;
- `compileall` and `git diff --check` passed;
- recovery coordinator, change transactions and checkpoint manager each have about 90% statement coverage;
- repository secret scan found only test fixtures, placeholders and parameter names; no supplied provider key is persisted.

## Architecture assessment

### What is now differentiated

UndoMesh's recovery path is not a thin Git undo wrapper. Every accepted mutation has pre/post hashes and a durable Change Journal. Large deletions are admitted before unlink, streamed into an encrypted content-defined chunk store, and separated into bounded warm retention or a 24-hour hot vault. Recovery itself is a new transaction with its own Journal, dependency lease, complete materialization, hash verification, second live-workspace conflict check, commit receipt and crash compensation.

Prompt rewind composes this storage layer with the append-only event model. A checkpoint can restore code, fork conversation, do both, or shadow later context with a summary. Original change journals and source session events remain immutable audit evidence. This improves on checkpoint systems that only track agent edits, only revert Git commits, or silently lose rollback ability when a local cache is cleaned.

### Safety invariants

1. No destructive mutation occurs until recoverability admission succeeds.
2. A restore does not write its first target until every target has passed conflict, dependency, authentication and disk-space preflight.
3. Garbage collection cannot remove leased restore dependencies.
4. Unknown live content is never overwritten automatically.
5. Restore success means the installed bytes were read back and matched the expected digest.
6. An interrupted restore either compensates to its pre-restore state or remains visibly conflicted with its lease retained.
7. Early permanent hot-vault cleanup is scope-bound and always requires a fresh human approval.

### Explicit limitations

- Atomic rename is per path, not a filesystem-wide atomic commit across many paths.
- Shell commands, databases, network calls, package registries and other external side effects are audited but not automatically reversible.
- A `both` rewind spans two durable domains. If code restore completes and the event-store fork fails, UndoMesh records `code_restore_completed=true` and the restore ID, but cannot pretend the combined action was atomic.
- The same-machine fallback key is an availability convenience, not enterprise key management. Key rotation, escrow and hardware-backed storage remain future work.
- Python Gear chunking and zlib prioritize portability over multi-gigabyte throughput.
- Tkinter provides a dependency-light client, but lacks a production desktop shell's accessibility, virtualization, auto-update and native diff/editor capabilities.
- Unlinking encrypted cache objects is not a forensic erase guarantee on SSDs, snapshots or synchronized storage.

## Self-check findings resolved in this pass

- Startup recovery previously allowed a damaged compensation payload to abort initialization. It now fails closed into `conflicted`, preserves the lease and keeps the application available for diagnosis.
- A cross-domain `both` rewind could restore code and then fail to fork conversation without exposing that partial result in the failure event. The failure audit now includes the restore ID and `code_restore_completed` flag.
- Desktop recovery formatting was separated into pure view-model functions and covered without requiring a display server.
- CLI discovery now exposes checkpoint, rewind and desktop entry points in test coverage.

## Prioritized improvements

### P0 — production hardening

1. Add an application-service command queue with progress, cancellation and resumable restore events. This removes the long-operation bottleneck from the single background event loop and gives the desktop client byte-level progress.
2. Authenticate recovery manifest metadata as one signed envelope and introduce key IDs, rotation, escrow/export and Keyring/DPAPI integration. A missing old key must be reported before retention removes the last readable copy.
3. Add a real review surface: file tree, structured diff, proposed tool calls, permission reason, transaction receipt and checkpoint health in one approval panel.
4. Run process-bound shell tools in an OS sandbox with resource limits and network policy. Current path and command rules are defense-in-depth, not containment.

### P1 — recovery quality and scale

1. Replace the pure Python hot path with native FastCDC and Zstandard; benchmark 1, 10 and 50 GiB incompressible, compressible and duplicate-heavy corpora.
2. Support filesystem reflinks/snapshots for same-volume hot quarantine and an optional immutable T3 object-store/Git-LFS adapter.
3. Offer explicit three-way text merge when live content conflicts, while retaining the current fail-closed default for binary or ambiguous files.
4. Serialize SQLite writes behind an application-owned worker and add migration/version tests, backup/restore of the event database and corruption drills.

### P2 — product maturity

1. Package signed Windows/macOS/Linux desktop installers with auto-update and crash reporting that redacts prompts, paths and credentials.
2. Virtualize long task/event lists, add search and pin/archive semantics, and make restore receipts exportable.
3. Expand multi-agent isolation to per-agent worktrees plus enforceable capability budgets and deterministic merge review.
4. Add live-provider conformance in opt-in CI, while keeping the deterministic offline suite as the release gate.

## Recommended next milestone

Treat the next release as production hardening rather than feature expansion. The most valuable vertical slice is: application command queue → resumable restore progress events → desktop review/diff panel → signed manifest and managed-key migration. It closes the largest operational and trust gaps without weakening the recovery model that now differentiates the project.

## Stage 19 follow-up — 2026-08-24

The first production-control-plane slice is now implemented. Layered user/project/directory/override instructions are loaded within a complete-file byte budget and expose local source diagnostics without injecting absolute host paths into the model prompt. A programmatic Lifecycle Hook Bus adds stable priority ordering, monotonic `deny > ask > allow > abstain` enforcement, fail-closed pre-tool errors and append-only audit events. App initialization now registers checkpoint tools before freezing the executor's default capability snapshot, fixing a real composition defect that previously made a registered rewind tool unavailable to the default agent.

Shell execution now uses a replaceable backend. Linux Bubblewrap supports host-read-only/project-write boundaries, network/PID namespaces, `prlimit` resource quotas and an optional inherited seccomp BPF FD. Windows Job Objects enforce kill-on-close, memory, CPU-time and process-count quotas but truthfully report no file/network isolation. Restore/Rewind/Audit now use the durable single-writer queue; Plugin activation binds an approved manifest digest to Hook/MCP loading and explicit Capability approval; Worktree merge binds approval to a verified Diff digest. Current evidence is 201 passed, 1 skipped and 18/18 fixed offline suites, including a key-free end-to-end interview demonstration.
