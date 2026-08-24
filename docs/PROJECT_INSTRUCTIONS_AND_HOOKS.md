# Project Instructions and Lifecycle Hooks

Stage 19 begins with two control-plane primitives that do not bypass the existing Query Loop or ToolExecutor.

## Layered project instructions

`ProjectInstructionLoader` reads trusted guidance in broad-to-specific order:

1. user directory: `~/.undomesh/AGENTS.override.md`, otherwise `AGENTS.md`;
2. project root;
3. each directory from the project root to the active working directory.

At each directory, `AGENTS.override.md` replaces `AGENTS.md`; configured fallback filenames are considered afterward. Empty files are ignored. The combined UTF-8 byte budget defaults to 32 KiB, files are never partially injected, and diagnostics report every source, scope, size and whether discovery was truncated.

```text
python -m undomesh --instructions
```

The content is inserted into the system prompt as project instructions. It is read as guidance only and cannot grant tools, widen paths or override permission rules.

Configuration:

- `UNDOMESH_INSTRUCTION_MAX_BYTES=32768`
- `UNDOMESH_INSTRUCTION_FALLBACK_NAMES=TEAM_GUIDE.md,.agents.md`
- `UNDOMESH_INSTRUCTION_USER_DIR=`; empty means `~/.undomesh`

## Lifecycle Hook Bus

`HookBus` supports four initial points:

- `pre_tool`: enforceable before tool execution;
- `post_tool`: observe successful results;
- `tool_failure`: observe returned errors, timeout and exceptions;
- `runtime_event`: synchronous observe-only access to appended lifecycle facts.

Hooks are registered programmatically with name, glob matcher, priority and failure policy. Stable ordering is priority descending then name. Pre-tool verdicts are monotonic:

```text
deny > ask > allow > abstain
```

A later allow cannot undo a deny. A pre-tool Hook exception fails closed by default; post/failure observers cannot rewrite a ToolResult. Every enforceable invocation, completion or failure is appended as `hook.invoked`, `hook.completed` or `hook.failed`, with ordinary event-store redaction applied.

```python
from undomesh.runtime import HookOutcome, HookPoint, HookVerdict

app.hook_bus.register(
    lambda ctx: HookOutcome(HookVerdict.DENY, "release branch is read-only"),
    point=HookPoint.PRE_TOOL,
    matcher="apply_patch_set",
    name="release-guard",
    priority=100,
)
```

Use `python -m undomesh --hooks` or REPL `/hooks` to inspect registrations. REPL `/instructions` shows the instruction chain.

## Security boundary

Project text is not enforcement. Hook enforcement cannot expand CapabilitySet, readable/writable roots or PermissionManager decisions. Repository declarative Hooks now require an explicitly approved SHA-256 digest and may only select built-in `allow`, `ask` or `deny` policy actions; arbitrary command, HTTP or code handlers remain forbidden.

## Shell sandbox backends

`UNDOMESH_SANDBOX_BACKEND` accepts `process`, `auto`, or `bubblewrap`. The portable default retains the existing process-group controls and does not claim kernel containment. On Linux, the explicit Bubblewrap backend exposes the host filesystem read-only, remounts only the project root writable, changes into a previously boundary-checked project directory, creates fresh process/device/temp mounts, and disables networking unless `UNDOMESH_SANDBOX_NETWORK_ENABLED=true`. `auto` selects it only when available.

Bubblewrap can additionally apply `prlimit` memory/CPU/process quotas and an inherited compiled seccomp BPF FD. Windows Job Objects provide kill-on-close and memory/CPU/process-count limits, while honestly reporting that Windows filesystem and networking are not isolated. Subprocess credentials are scrubbed for every backend.

## Stage 19 completion boundary

The durable application queue, progress/cancellation, explicit interrupted-job resume, authenticated Manifest envelope, Key ID rotation/audit and trusted non-executable Hook loader are implemented. Enterprise KMS/HSM escrow, Windows filesystem/network containment and a product UI for approving Hook digests remain deployment/product integrations. See `docs/STAGES_19_21_IMPLEMENTATION.md`.
