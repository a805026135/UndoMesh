"""Non-secret provider preference persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from miniclaude.core.config import MiniClaudeConfig
from miniclaude.llm.providers import get_provider, resolve_base_url


class ProviderSettingsStore:
    """Persist provider/model/base URL; API keys are deliberately excluded."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {key: str(value[key]) for key in ("provider", "model", "api_base_url") if value.get(key)}

    def save(self, *, provider: str, model: str, api_base_url: str) -> None:
        get_provider(provider)
        payload = {"schema_version": 1, "provider": provider, "model": model.strip(), "api_base_url": resolve_base_url(provider, api_base_url)}
        if not payload["model"]:
            raise ValueError("A model name is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def apply(self, config: MiniClaudeConfig) -> MiniClaudeConfig:
        stored = self.load()
        updates: dict[str, Any] = {}
        env_map = {"provider": "MINICLAUDE_PROVIDER", "model": "MINICLAUDE_MODEL", "api_base_url": "MINICLAUDE_API_BASE_URL"}
        for key, env_name in env_map.items():
            if key in stored and env_name not in os.environ:
                updates[key] = stored[key]
        return config.model_copy(update=updates) if updates else config
