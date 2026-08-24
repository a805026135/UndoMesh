"""Configuration management using Pydantic Settings."""

from __future__ import annotations

import os
import base64
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings
from miniclaude.llm.providers import get_provider, resolve_base_url


class MiniClaudeConfig(BaseSettings):
    """Central configuration for MiniClaude, loaded from env vars and .env file."""

    model_config = {"env_prefix": "MINICLAUDE_", "env_file": ".env", "extra": "ignore"}

    # ── LLM Settings ──────────────────────────────────────────────
    model: str = Field(
        default="mimo-v2.5-pro",
        description="Model identifier (e.g. mimo-v2.5-pro)",
    )
    provider: str = Field(default="custom", description="Model provider preset")
    api_key_override: str = Field(default="", exclude=True, repr=False)
    max_tokens: int = Field(default=8192, description="Max output tokens per response")
    temperature: float = Field(default=0.6, description="Sampling temperature")

    # ── API Settings ──────────────────────────────────────────────
    api_base_url: str = Field(
        default="https://token-plan-cn.xiaomimimo.com/v1",
        description="OpenAI-compatible API base URL",
    )

    @property
    def api_key(self) -> str:
        """Read API key from environment."""
        preset = get_provider(self.provider)
        key = self.api_key_override
        for name in ("MINICLAUDE_API_KEY", *preset.api_key_env, "MIMO_API_KEY"):
            key = key or os.getenv(name, "")
        if not key:
            raise ValueError(
                "API key not set. Please set MINICLAUDE_API_KEY or MIMO_API_KEY "
                "in your .env file or shell environment."
            )
        return key

    @property
    def effective_api_base_url(self) -> str:
        return resolve_base_url(self.provider, self.api_base_url)

    # ── Context & Budget ──────────────────────────────────────────
    context_limit: int = Field(
        default=131072,
        description="Maximum context window in tokens",
    )
    externalize_threshold: int = Field(
        default=2000,
        description="Character count threshold to externalize tool results",
    )
    reserved_output_tokens: int = Field(
        default=16_000,
        description="Tokens reserved for model output",
    )

    # ── Security ──────────────────────────────────────────────────
    allow_shell: bool = Field(
        default=True,
        description="Whether shell command execution is allowed",
    )
    transactional_changes_only: bool = Field(
        default=True,
        description="Expose only durable ChangeSet mutation tools; direct writes and shell are legacy opt-in",
    )
    transaction_committed_retention_days: int = Field(default=30, ge=0)
    transaction_terminal_retention_days: int = Field(default=7, ge=0)
    transaction_trash_grace_days: int = Field(default=1, ge=0)
    transaction_orphan_grace_hours: int = Field(default=1, ge=0)
    transaction_max_backup_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    transaction_auto_prune: bool = Field(default=True)
    transaction_prune_interval_hours: float = Field(default=6.0, ge=0)
    transaction_backup_encryption: bool = Field(default=True)
    transaction_backup_key: str = Field(default="", exclude=True, repr=False)
    transaction_chunk_min_bytes: int = Field(default=16 * 1024, ge=1024)
    transaction_chunk_avg_bytes: int = Field(default=64 * 1024, ge=1024)
    transaction_chunk_max_bytes: int = Field(default=256 * 1024, ge=1024)
    transaction_hot_threshold_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    transaction_hot_ttl_hours: float = Field(default=24.0, gt=0)
    transaction_hot_max_backup_bytes: int = Field(default=50 * 1024 * 1024 * 1024, ge=1)
    transaction_hot_minimum_free_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=0)
    confirm_dangerous: bool = Field(
        default=True,
        description="Whether to prompt user for confirmation on dangerous ops",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Root directory for file operations (boundary)",
    )

    # ── Memory ────────────────────────────────────────────────────
    memory_db: str = Field(
        default="data/memory/miniclaude.db",
        description="Path to the SQLite memory database",
    )
    memory_enabled: bool = Field(
        default=True,
        description="Whether the memory system is active",
    )
    sandbox_backend: str = Field(
        default="process",
        description="Shell sandbox backend: process, auto, or bubblewrap",
    )
    sandbox_network_enabled: bool = Field(
        default=False,
        description="Allow network access inside an OS sandbox backend",
    )
    sandbox_memory_bytes: int = Field(default=0, ge=0)
    sandbox_cpu_seconds: int = Field(default=0, ge=0)
    sandbox_process_count: int = Field(default=0, ge=0)
    sandbox_seccomp_fd: int = Field(
        default=-1, ge=-1,
        description="Advanced Linux: inherited FD containing a compiled seccomp BPF program",
    )
    instruction_max_bytes: int = Field(default=32 * 1024, ge=0)
    instruction_fallback_names: str = Field(
        default="",
        description="Comma-separated project instruction fallback filenames",
    )
    instruction_user_dir: str = Field(
        default="",
        description="Optional user instruction directory; empty uses ~/.miniclaude",
    )
    tool_timeout_seconds: float = Field(
        default=120.0,
        description="Default timeout applied to one tool execution",
    )
    event_db: str = Field(
        default="data/runtime/events.db",
        description="Path to the durable runtime event database",
    )
    job_db: str = Field(
        default="data/runtime/jobs.db",
        description="Path to the durable application command queue",
    )
    telemetry_enabled: bool = Field(
        default=False,
        description="Enable redacted local/exported runtime telemetry",
    )
    skill_eval_db: str = Field(
        default="data/skills/evaluations.db",
        description="Path to versioned Skill evaluation evidence",
    )

    # ── Logging ───────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging level")

    # ── Paths ─────────────────────────────────────────────────────
    @property
    def base_dir(self) -> Path:
        """Base directory of the MiniClaude installation."""
        return Path(__file__).resolve().parent.parent.parent

    @property
    def data_dir(self) -> Path:
        """Data directory for runtime artifacts."""
        d = self.base_dir / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def externalized_dir(self) -> Path:
        """Directory for externalized large tool results."""
        d = self.data_dir / "externalized"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def sessions_dir(self) -> Path:
        """Directory for session history."""
        d = self.data_dir / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def memory_db_path(self) -> Path:
        """Full path to the memory database."""
        p = self.base_dir / self.memory_db
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def event_db_path(self) -> Path:
        """Full path to the durable runtime event database."""
        p = self.base_dir / self.event_db
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def job_db_path(self) -> Path:
        p = self.base_dir / self.job_db
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def skill_eval_db_path(self) -> Path:
        p = self.base_dir / self.skill_eval_db
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def provider_settings_path(self) -> Path:
        return self.data_dir / "config" / "provider.json"

    def effective_project_dir(self) -> Path:
        """Return the effective project directory boundary."""
        if self.project_dir:
            return Path(self.project_dir).resolve()
        return Path.cwd().resolve()

    @property
    def instruction_user_config_dir(self) -> Path:
        if self.instruction_user_dir:
            return Path(self.instruction_user_dir).expanduser().resolve()
        return (Path.home() / ".miniclaude").resolve()

    @property
    def instruction_fallback_name_tuple(self) -> tuple[str, ...]:
        return tuple(
            value.strip() for value in self.instruction_fallback_names.split(",")
            if value.strip()
        )

    def transaction_retention_policy(self):
        from miniclaude.execution.transactions import RetentionPolicy
        return RetentionPolicy(
            committed_days=self.transaction_committed_retention_days,
            terminal_days=self.transaction_terminal_retention_days,
            trash_grace_days=self.transaction_trash_grace_days,
            orphan_grace_hours=self.transaction_orphan_grace_hours,
            max_backup_bytes=self.transaction_max_backup_bytes,
        )

    def transaction_chunking_policy(self):
        from miniclaude.execution.backup_store import ChunkingPolicy
        return ChunkingPolicy(
            minimum=self.transaction_chunk_min_bytes,
            average=self.transaction_chunk_avg_bytes,
            maximum=self.transaction_chunk_max_bytes,
        )

    def transaction_hot_vault_policy(self):
        from miniclaude.execution.transactions import HotVaultPolicy
        return HotVaultPolicy(
            threshold_bytes=self.transaction_hot_threshold_bytes,
            ttl_hours=self.transaction_hot_ttl_hours,
            max_backup_bytes=self.transaction_hot_max_backup_bytes,
            minimum_free_bytes=self.transaction_hot_minimum_free_bytes,
        )

    def transaction_backup_key_bytes(self) -> bytes | None:
        value = self.transaction_backup_key.strip()
        if not value:
            return None
        try:
            decoded = bytes.fromhex(value) if len(value) == 64 else base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Transaction backup key must be 64 hex characters or base64") from exc
        if len(decoded) != 32:
            raise ValueError("Transaction backup key must decode to exactly 32 bytes")
        return decoded


# Singleton instance
_config: MiniClaudeConfig | None = None


def get_config() -> MiniClaudeConfig:
    """Get or create the global config singleton."""
    global _config
    if _config is None:
        _config = MiniClaudeConfig()
    return _config


def reset_config() -> None:
    """Reset the config singleton (useful for testing)."""
    global _config
    _config = None
