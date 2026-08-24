"""Block-level provenance attached to runtime facts and execution requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TrustLevel(StrEnum):
    TRUSTED_SYSTEM = "trusted_system"
    TRUSTED_USER = "trusted_user"
    WORKSPACE = "workspace"
    GENERATED = "generated"
    EXTERNAL = "external"
    UNTRUSTED = "untrusted"


class SourceType(StrEnum):
    SYSTEM_INSTRUCTION = "system_instruction"
    USER_INPUT = "user_input"
    WORKSPACE_FILE = "workspace_file"
    GENERATED_FILE = "generated_file"
    TOOL_RESULT = "tool_result"
    WEB_CONTENT = "web_content"
    MCP_CONTENT = "mcp_content"
    MEMORY = "memory"
    SKILL = "skill"
    SUBAGENT = "subagent"
    MODEL_OUTPUT = "model_output"


@dataclass(frozen=True, slots=True)
class Provenance:
    source_type: SourceType
    trust_level: TrustLevel
    source_id: str
    parent_source_ids: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "trust_level": self.trust_level.value,
            "source_id": self.source_id,
            "parent_source_ids": list(self.parent_source_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Provenance":
        return cls(
            source_type=SourceType(value["source_type"]),
            trust_level=TrustLevel(value["trust_level"]),
            source_id=value["source_id"],
            parent_source_ids=tuple(value.get("parent_source_ids", ())),
            metadata=tuple(sorted((value.get("metadata") or {}).items())),
        )
