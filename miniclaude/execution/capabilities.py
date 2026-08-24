"""Least-privilege capabilities supplied to one tool execution scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from miniclaude.provenance.models import Provenance


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    allowed_tools: frozenset[str]
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    network_enabled: bool = False
    shell_enabled: bool = False
    max_processes: int = 1

    @classmethod
    def for_project(
        cls,
        project_root: Path,
        allowed_tools: set[str] | frozenset[str],
        *,
        shell_enabled: bool = False,
        network_enabled: bool = False,
    ) -> "CapabilitySet":
        root = project_root.resolve()
        return cls(
            allowed_tools=frozenset(allowed_tools),
            readable_roots=(root,),
            writable_roots=(root,),
            shell_enabled=shell_enabled,
            network_enabled=network_enabled,
        )

    def restrict_tools(self, allowed_tools: set[str] | frozenset[str]) -> "CapabilitySet":
        """Return a child capability that can never exceed the parent."""
        return CapabilitySet(
            allowed_tools=self.allowed_tools.intersection(allowed_tools),
            readable_roots=self.readable_roots,
            writable_roots=self.writable_roots,
            network_enabled=self.network_enabled,
            shell_enabled=self.shell_enabled and "run_command" in allowed_tools,
            max_processes=self.max_processes,
        )


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    session_id: str = "unknown"
    turn_id: str = "unknown"
    step_id: str = "unknown"
    agent_id: str = "root"
    user_input: str = ""
    capability_set: CapabilitySet | None = None
    provenance: tuple[Provenance, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
