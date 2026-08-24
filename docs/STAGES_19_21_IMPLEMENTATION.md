# Stages 19–21 Implementation and Trust Boundaries

## Status

The local implementation is complete at the reusable-core and adapter level. Platform- or service-dependent claims remain conditional: Windows Job Objects do not isolate files or networking; Bubblewrap seccomp requires a caller-supplied compiled BPF file descriptor; SWE-bench and Terminal-Bench require pinned external datasets, containers and a model key; release artifacts require real platform signing identities before publication.

## Stage 19 — Production control plane

### Application service and durable command queue

`AppService` owns a SQLite `JobStore` and `SingleWriterCommandQueue`. Jobs transition through `queued`, `running`, `succeeded`, `failed`, `cancelled` and `interrupted`. Only one mutating handler executes at a time. Progress and cancellation are persisted. Startup converts abandoned `running` jobs to `interrupted`; it never silently re-executes a possibly non-idempotent operation. Resume is explicit. `python -m undomesh --app-server` exposes newline-delimited JSON-RPC over stdio, and the same service is used by the Python SDK.

### Authenticated manifests and rotation

`ManifestAuthenticator` signs the canonical complete envelope using HMAC-SHA256 and records a Key ID. `LocalKeyRing.rotate()` creates a new active 256-bit key while retaining old keys for reads. `audit()` reports available, missing and unreferenced keys before retention or migration. Export is explicit and must be placed behind human approval by the caller. The local adapter is owner-only storage, not enterprise KMS/HSM escrow.

### Sandbox

- `process`: portable process-group control, no kernel containment claim.
- `bubblewrap`: Linux host filesystem read-only, project writable, PID namespace, optional network namespace, optional `prlimit` address-space/CPU/process limits, and optional inherited compiled seccomp BPF FD.
- `windows_job`: Windows Job Object kill-on-close plus job memory, CPU time and active-process limits. Windows filesystem and network isolation remain false in its capability report.

The application fails instead of pretending requested `prlimit` limits exist when the executable is unavailable. Subprocess credentials are scrubbed under every backend.

### External hooks

Repository Hook JSON is never executable. A human-approved SHA-256 digest is required, only `pre_tool` built-in `allow`, `ask` and `deny` actions are accepted, and unknown fields such as `command` are rejected. These decisions still cannot widen CapabilitySet, path roots or PermissionManager policy.

## Stage 20 — Ecosystem and engineering

### MCP

The MCP client supports a transport-neutral JSON-RPC interface, STDIO and Streamable HTTP POST, optional in-memory OAuth bearer tokens, initialization, lazy `tools/list` schemas and `tools/call`. Remote tools are registered as `mcp__server__tool` `BaseTool` proxies, so ToolExecutor security, approvals, provenance, timeout and Hooks remain mandatory. The current HTTP adapter handles JSON responses; full SSE session resumption and interactive OAuth authorization-code UX remain interoperability extensions.

### Telemetry

Telemetry is disabled by default. When enabled, it records spans/metrics through an injected exporter and redacts key/token/secret/password/authorization/prompt/content attributes. Optional OpenTelemetry packages are declared separately so the core has no exporter dependency.

### Worktree and review

`GitWorktreeManager` resolves an immutable baseline commit, creates a real branch-backed worktree, exports binary Diff, runs argv-only verification, commits the candidate, refuses a merge when the parent is dirty, and removes the worktree without force by default. Desktop now includes a Review window with file list, additions/deletions, raw Diff and recent transaction receipts.

### Native performance backend

`CompressionBackend` selects optional FastCDC/Zstandard when installed and reports the actual backend; otherwise it deterministically falls back to Python Gear/zlib. The existing recovery store remains format-compatible and does not silently rewrite old chunks. Large 1/10/50 GiB measurements must be run on declared hardware rather than fabricated in CI.

## Stage 21 — SDK, protocols, evaluation and distribution

### SDK and RPC/ACP

`UndoMeshClient` is an asynchronous SDK over `AppService`: submit, get, cancel, resume, stream and prompt. `JSONRPCDispatcher` exposes job operations plus `agent/session/new` and `agent/prompt` ACP-style methods. Runtime, recovery and permission logic stay in the application service.

### Plugins and controlled Code Mode

Plugin manifests package relative Skill paths, a declarative Hook manifest, MCP dependencies and optional UI metadata. Names and paths are validated; package traversal is rejected. Controlled Code Mode accepts at most 32 declarative tool steps containing only `tool` and `arguments`. Each step crosses ToolExecutor and stops on failure; arbitrary Python/shell evaluation is intentionally absent.

### Standard evaluation

`benchmarks/standard_suites.json` defines SWE-bench Verified, Terminal-Bench and the recovery/security suite with explicit external prerequisites. The summarizer reports attempts, successes, tokens, cost, wall time and a Wilson 95% interval. No SWE-bench or Terminal-Bench score is claimed until a pinned external run produces evidence.

### Desktop distribution and updates

The desktop build entry produces PyInstaller artifacts on Windows, macOS and Linux CI. `sign_update.py` creates an Ed25519-signed artifact envelope. `UpdateVerifier` verifies signature and SHA-256 before atomic staging for next-launch activation. Crash reports redact prompt/content/path and credential fields. OS-native Authenticode/notarization/package signing still requires project-owned certificates and is deliberately outside source code and repository secrets.

## Verification

For interview-facing vertical evidence, `python scripts/demo_interview.py` runs a real durable restore through the background queue, proves that newer user content is preserved on conflict, and performs a verified Git Worktree merge bound to an approved Diff digest. It needs no model key or network.

Run:

```text
python -m pytest -q
python scripts/run_offline_benchmarks.py
python scripts/release_check.py
python -m compileall -q undomesh
git diff --check
```

The fixed offline suite validates state machines, trust boundaries and adapters. It is not a model-quality leaderboard or proof that unavailable external platforms were exercised.
