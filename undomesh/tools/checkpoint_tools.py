"""Prompt checkpoint inspection and rewind tools."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from undomesh.runtime.checkpoints import CheckpointManager, RewindMode
from undomesh.tools.base import BaseTool, ToolResult


class ListPromptCheckpointsTool(BaseTool):
    def __init__(self, manager: CheckpointManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "list_prompt_checkpoints"

    @property
    def description(self) -> str:
        return "List prompt recovery points with code transaction health and conflicts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            checkpoints = self.manager.list_checkpoints()
        except (OSError, ValueError) as exc:
            return self._error(f"Checkpoint lookup failed: {exc}")
        payload = [asdict(item) for item in checkpoints]
        return self._success(json.dumps(payload, ensure_ascii=False), metadata={"count": len(payload)})


class RewindCheckpointTool(BaseTool):
    def __init__(self, manager: CheckpointManager) -> None:
        self.manager = manager

    @property
    def name(self) -> str:
        return "rewind_checkpoint"

    @property
    def description(self) -> str:
        return "Rewind code, conversation, both, or summarize history from one prompt checkpoint."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "checkpoint_id": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": [item.value for item in RewindMode],
                "description": "Defaults to both",
            },
        }

    def _get_required_params(self) -> list[str]:
        return ["checkpoint_id"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            receipt = self.manager.rewind(
                str(kwargs.get("checkpoint_id", "")),
                RewindMode(str(kwargs.get("mode", "both"))),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return self._error(f"Checkpoint rewind failed: {exc}", metadata={"denied": True})
        payload = asdict(receipt)
        payload["mode"] = receipt.mode.value
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)
