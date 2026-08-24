"""Validated package metadata for Skills, Hooks and MCP dependencies."""

from __future__ import annotations

import json
from pathlib import Path
import re

from pydantic import BaseModel, Field, field_validator


class MCPDependency(BaseModel):
    name: str
    transport: str
    endpoint: str


class PluginManifest(BaseModel):
    schema_version: int = 1
    name: str
    version: str
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    hook_manifest: str = ""
    mcp: list[MCPDependency] = Field(default_factory=list)
    ui: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", value):
            raise ValueError("plugin name must be a lowercase package identifier")
        return value

    @field_validator("skills", "hook_manifest")
    @classmethod
    def relative_paths_only(cls, value):
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item and (Path(item).is_absolute() or ".." in Path(item).parts):
                raise ValueError("plugin paths must stay relative to the package")
        return value


def load_plugin_manifest(path: Path) -> PluginManifest:
    manifest = PluginManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if manifest.schema_version != 1:
        raise ValueError("unsupported plugin manifest schema")
    return manifest
