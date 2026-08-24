"""Controlled tool programs: code-like batching without an unrestricted interpreter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolPresentation(StrEnum):
    NATIVE = "native"
    CODE = "code"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class ToolStep:
    tool: str
    arguments: dict[str, Any]


class ControlledToolProgram:
    MAX_STEPS = 32

    @classmethod
    def parse(cls, document: dict[str, Any]) -> tuple[ToolStep, ...]:
        raw_steps = document.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) > cls.MAX_STEPS:
            raise ValueError("tool program requires at most 32 steps")
        steps: list[ToolStep] = []
        for raw in raw_steps:
            if not isinstance(raw, dict) or set(raw) != {"tool", "arguments"}:
                raise ValueError("each step must contain only tool and arguments")
            if not isinstance(raw["tool"], str) or not isinstance(raw["arguments"], dict):
                raise ValueError("invalid tool step")
            steps.append(ToolStep(raw["tool"], raw["arguments"]))
        return tuple(steps)

    @staticmethod
    async def execute(steps: tuple[ToolStep, ...], executor: Any, **context: Any) -> list[Any]:
        results = []
        for step in steps:
            # Every operation still crosses the ordinary ToolExecutor boundary.
            result = await executor.execute_single(step.tool, step.arguments, **context)
            results.append(result)
            if not result.success:
                break
        return results
