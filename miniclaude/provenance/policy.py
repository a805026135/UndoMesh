"""Deterministic block-level source-to-sink policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from miniclaude.provenance.models import Provenance, TrustLevel


class SinkType(StrEnum):
    NONE = "none"
    SHELL_EXECUTE = "shell.execute"
    FILESYSTEM_WRITE = "filesystem.write"
    FILESYSTEM_READ_SENSITIVE = "filesystem.read_sensitive"
    NETWORK_SEND = "network.send"
    MCP_WRITE = "mcp.write"
    MEMORY_PERSIST = "memory.persist"
    AGENT_DELEGATE = "agent.delegate"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: PolicyAction
    sink: SinkType
    reason: str
    source_ids: tuple[str, ...] = ()


class ProvenancePolicy:
    """Evaluate untrusted influence before a request reaches a sensitive sink."""

    SENSITIVE_PATTERNS = (
        re.compile(r"(^|[\\/])\.env($|\.)", re.IGNORECASE),
        re.compile(r"(^|[\\/])\.ssh([\\/]|$)", re.IGNORECASE),
        re.compile(r"(^|[\\/])\.aws([\\/]|$)", re.IGNORECASE),
        re.compile(r"id_(rsa|ed25519)$", re.IGNORECASE),
        re.compile(r"(credential|secret|private[_-]?key)", re.IGNORECASE),
        re.compile(r"\.(pem|key)$", re.IGNORECASE),
    )

    def evaluate(
        self,
        tool_name: str,
        params: dict,
        provenance: tuple[Provenance, ...],
    ) -> PolicyDecision:
        sink = self.classify_sink(tool_name, params)
        risky = tuple(
            item
            for item in provenance
            if item.trust_level in {
                TrustLevel.WORKSPACE,
                TrustLevel.EXTERNAL,
                TrustLevel.UNTRUSTED,
            }
        )
        source_ids = tuple(item.source_id for item in risky)
        if not risky or sink == SinkType.NONE:
            return PolicyDecision(PolicyAction.ALLOW, sink, "No untrusted source reaches a sensitive sink")

        has_external = any(
            item.trust_level in {TrustLevel.EXTERNAL, TrustLevel.UNTRUSTED}
            for item in risky
        )
        if sink == SinkType.FILESYSTEM_READ_SENSITIVE:
            return PolicyDecision(
                PolicyAction.DENY,
                sink,
                f"Untrusted content cannot reach {sink.value}",
                source_ids,
            )
        if sink in {SinkType.NETWORK_SEND, SinkType.MCP_WRITE}:
            action = PolicyAction.DENY if has_external else PolicyAction.ASK
            return PolicyDecision(
                action,
                sink,
                f"Repository or untrusted content influences {sink.value}",
                source_ids,
            )
        if sink == SinkType.FILESYSTEM_WRITE and not has_external:
            return PolicyDecision(
                PolicyAction.ALLOW,
                sink,
                "Workspace content may update files inside the granted write roots",
                source_ids,
            )
        if sink in {
            SinkType.SHELL_EXECUTE,
            SinkType.FILESYSTEM_WRITE,
            SinkType.MEMORY_PERSIST,
            SinkType.AGENT_DELEGATE,
        }:
            return PolicyDecision(
                PolicyAction.ASK,
                sink,
                f"Untrusted content influences {sink.value}",
                source_ids,
            )
        return PolicyDecision(PolicyAction.ALLOW, sink, "Policy allows this flow", source_ids)

    def classify_sink(self, tool_name: str, params: dict) -> SinkType:
        if tool_name == "run_command":
            return SinkType.SHELL_EXECUTE
        if tool_name in {"write_file", "edit_file", "apply_patch_set", "rollback_change_set"}:
            return SinkType.FILESYSTEM_WRITE
        if tool_name == "read_file" and self.is_sensitive_path(str(params.get("path", ""))):
            return SinkType.FILESYSTEM_READ_SENSITIVE
        if tool_name in {"web_fetch", "http_request", "network_send"}:
            return SinkType.NETWORK_SEND
        if tool_name.startswith("mcp_") and params.get("write", True):
            return SinkType.MCP_WRITE
        if tool_name == "memory_save":
            return SinkType.MEMORY_PERSIST
        if tool_name == "spawn_agent":
            return SinkType.AGENT_DELEGATE
        return SinkType.NONE

    def is_sensitive_path(self, path: str) -> bool:
        normalized = str(Path(path)).replace("/", "\\")
        return any(pattern.search(normalized) for pattern in self.SENSITIVE_PATTERNS)
