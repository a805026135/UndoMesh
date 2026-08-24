"""Small deterministic validator for tool input schemas."""

from __future__ import annotations

from typing import Any

from undomesh.tools.base import BaseTool


JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def validate_tool_input(tool: BaseTool, params: dict[str, Any]) -> list[str]:
    """Validate required fields and the schema subset used by built-in tools."""
    errors: list[str] = []
    for name in tool._get_required_params():
        if name not in params:
            errors.append(f"Missing required parameter: {name}")

    for name, value in params.items():
        schema = tool.parameters.get(name)
        if schema is None:
            errors.append(f"Unknown parameter: {name}")
            continue
        expected_name = schema.get("type")
        expected = JSON_TYPES.get(expected_name)
        if expected and not isinstance(value, expected):
            errors.append(
                f"Parameter '{name}' must be {expected_name}, got {type(value).__name__}"
            )
            continue
        if expected_name == "integer" and isinstance(value, bool):
            errors.append(f"Parameter '{name}' must be integer, got bool")
        allowed = schema.get("enum")
        if allowed is not None and value not in allowed:
            errors.append(f"Parameter '{name}' must be one of: {allowed}")
    return errors
