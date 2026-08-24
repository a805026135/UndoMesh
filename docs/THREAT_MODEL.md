# Threat Model

Protected assets are workspace integrity, credentials, user-controlled approval decisions, append-only event history and externalized artifacts.

Covered threats include path traversal and symlink escape, dangerous shell commands, sensitive file access, indirect prompt injection from workspace/web/MCP/memory/sub-agent content, secret leakage into audit/events, tool timeout, capability escalation and conflicting parallel writes.

Controls include canonical workspace roots, capability subsets, deny-first permission evaluation, provenance-aware source-to-sink decisions, payload redaction, structured task contracts and isolated write workspaces.

Not covered: a malicious or compromised model provider, host kernel compromise, arbitrary native extension escape, TLS interception, supply-chain compromise in dependencies, perfect semantic prompt-injection detection, or safe automatic merging of independent workspaces. API keys entered in the GUI are memory-only; environment variables remain the recommended non-interactive mechanism.
