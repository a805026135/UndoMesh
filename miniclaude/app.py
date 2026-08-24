"""MiniClaude application class - wires all subsystems together."""

from __future__ import annotations

import logging
import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from miniclaude.agents.causal import CausalTaskLedger, CausalWorktreeCoordinator
from miniclaude.agents.orchestrator import Orchestrator
from miniclaude.context.manager import ContextManager
from miniclaude.core.config import get_config, MiniClaudeConfig
from miniclaude.core.context import ConversationContext
from miniclaude.core.loop import QueryLoop
from miniclaude.core.tool_executor import ToolExecutor
from miniclaude.llm.client import ClaudeClient
from miniclaude.memory.compressor import MemoryCompressor
from miniclaude.memory.extractor import MemoryExtractor
from miniclaude.memory.retriever import MemoryRetriever
from miniclaude.memory.store import MemoryStore
from miniclaude.security.auditor import SecurityAuditor
from miniclaude.security.permission import PermissionManager
from miniclaude.runtime.store import SQLiteEventStore
from miniclaude.runtime.events import EventType
from miniclaude.runtime.dispatcher import LifecycleDispatcher
from miniclaude.runtime.hooks import HookBus
from miniclaude.runtime.checkpoints import CheckpointManager
from miniclaude.runtime.projection import project_messages
from miniclaude.skills.catalog import SkillCatalog
from miniclaude.skills.router import SkillRouter
from miniclaude.skills.evolution import SkillVersionStore
from miniclaude.llm.provider_settings import ProviderSettingsStore
from miniclaude.execution.transactions import DurableChangeEngine
from miniclaude.tools.registry import ToolRegistry
from miniclaude.tools.memory_tools import MemorySaveTool, MemorySearchTool
from miniclaude.ui.repl import REPL

logger = logging.getLogger(__name__)


class MiniClaudeApp:
    """Main application that orchestrates all 8 subsystems.

    Architecture:
    ┌─────────────────────────────────────────────────┐
    │  REPL (UI)                                      │
    ├─────────────────────────────────────────────────┤
    │  QueryLoop (Core Engine)                        │
    │  ┌──────────┐ ┌──────────┐ ┌─────────────────┐ │
    │  │ LLM      │ │ Tool     │ │ Context         │ │
    │  │ Client   │ │ Executor │ │ Manager         │ │
    │  └──────────┘ └──────────┘ └─────────────────┘ │
    ├─────────────────────────────────────────────────┤
    │  ┌──────────┐ ┌──────────┐ ┌─────────────────┐ │
    │  │ Skill    │ │ Memory   │ │ Multi-Agent     │ │
    │  │ Router   │ │ System   │ │ Orchestrator    │ │
    │  └──────────┘ └──────────┘ └─────────────────┘ │
    ├─────────────────────────────────────────────────┤
    │  Security & Permissions Layer                   │
    └─────────────────────────────────────────────────┘
    """

    def __init__(self, config: MiniClaudeConfig | None = None) -> None:
        raw_config = config or get_config()
        self.provider_settings = ProviderSettingsStore(raw_config.provider_settings_path)
        self.config = raw_config if config else self.provider_settings.apply(raw_config)

        # Core components
        self.llm_client: ClaudeClient | None = None
        self.tool_registry: ToolRegistry | None = None
        self.tool_executor: ToolExecutor | None = None
        self.context: ConversationContext | None = None
        self.event_store: SQLiteEventStore | None = None
        self.checkpoint_manager: CheckpointManager | None = None
        self.dispatcher: LifecycleDispatcher | None = None
        self.hook_bus: HookBus | None = None
        self.query_loop: QueryLoop | None = None

        # Skill system
        self.skill_catalog: SkillCatalog | None = None
        self.skill_router: SkillRouter | None = None
        self.skill_version_store: SkillVersionStore | None = None

        # Memory system
        self.memory_store: MemoryStore | None = None
        self.memory_retriever: MemoryRetriever | None = None
        self.memory_extractor: MemoryExtractor | None = None
        self.memory_compressor: MemoryCompressor | None = None

        # Context management
        self.context_manager: ContextManager | None = None

        # Multi-agent
        self.orchestrator: Orchestrator | None = None
        self.causal_ledger: CausalTaskLedger | None = None
        self.causal_coordinator: CausalWorktreeCoordinator | None = None

        # Security
        self.permission_manager: PermissionManager | None = None
        self.auditor: SecurityAuditor | None = None

        # UI
        self.repl: REPL | None = None

        self._initialized = False
        self._retention_task: asyncio.Task[None] | None = None
        self.change_engine = DurableChangeEngine(
            self.config.effective_project_dir(),
            retention_policy=self.config.transaction_retention_policy(),
            backup_encryption=self.config.transaction_backup_encryption,
            backup_encryption_key=self.config.transaction_backup_key_bytes(),
            chunking_policy=self.config.transaction_chunking_policy(),
            hot_vault_policy=self.config.transaction_hot_vault_policy(),
        )
        project_identity = hashlib.sha256(
            str(self.config.effective_project_dir()).encode("utf-8")
        ).hexdigest()[:16]
        worktree_root = (
            Path(tempfile.gettempdir())
            / "miniclaude-causal-worktrees"
            / project_identity
        )
        self.causal_ledger = CausalTaskLedger(self.config.causal_db_path)
        self.causal_coordinator = CausalWorktreeCoordinator(
            self.config.effective_project_dir(), worktree_root, self.causal_ledger
        )

    async def test_provider_connection(self, candidate: MiniClaudeConfig) -> str:
        """Test candidate credentials without changing the active client."""
        client = ClaudeClient(candidate)
        try:
            response = await client.test_connection()
            return response.text or "Connected"
        finally:
            await client.close()

    async def configure_provider(
        self,
        *,
        provider: str,
        model: str,
        api_base_url: str,
        api_key: str,
        persist: bool = True,
    ) -> None:
        candidate = self.config.model_copy(
            update={
                "provider": provider,
                "model": model.strip(),
                "api_base_url": api_base_url.strip(),
                "api_key_override": api_key.strip() or self.config.api_key_override,
            }
        )
        # Resolve and validate before mutating active state.
        candidate.effective_api_base_url
        candidate.api_key
        if persist:
            self.provider_settings.save(
                provider=provider,
                model=candidate.model,
                api_base_url=candidate.api_base_url,
            )
        self.config = candidate
        if self.llm_client:
            await self.llm_client.reconfigure(candidate)
        if self.query_loop:
            self.query_loop.config = candidate
        if self.tool_executor:
            self.tool_executor.config = candidate

    async def initialize(self) -> None:
        """Initialize all subsystems in dependency order."""
        if self._initialized:
            return

        logger.info("Initializing MiniClaude v0.1.0 ...")
        recovered = self.change_engine.recover_incomplete()
        if recovered:
            logger.warning("Recovered %d incomplete change transactions", len(recovered))
        recovered_restores = self.change_engine.recover_incomplete_restores()
        if recovered_restores:
            logger.warning("Recovered %d incomplete restore transactions", len(recovered_restores))
        if self.config.transaction_auto_prune:
            prune_report = self.change_engine.prune(dry_run=False)
            if prune_report.eligible_transactions or prune_report.released_bytes:
                logger.info(
                    "Transaction retention expired %d receipts and released %d bytes",
                    len(prune_report.eligible_transactions), prune_report.released_bytes,
                )
        logger.info("Model: %s, Context limit: %d", self.config.model, self.config.context_limit)

        # === 1. LLM Client ===
        self.llm_client = ClaudeClient(self.config)
        logger.info("✓ LLM Client initialized")

        # === 2. Tool Registry + Tools ===
        self.tool_registry = ToolRegistry(self.config)
        self.tool_registry.register_all()
        logger.info("✓ Tool Registry: %d tools", len(self.tool_registry))

        # === 3. Memory System ===
        if self.config.memory_enabled:
            self.memory_store = MemoryStore(self.config.memory_db_path)
            self.memory_retriever = MemoryRetriever(self.memory_store)
            self.memory_extractor = MemoryExtractor(self.llm_client, self.memory_store)
            self.memory_compressor = MemoryCompressor(self.memory_store)

            # Register memory tools
            self.tool_registry.register(MemorySaveTool(self.config, self.memory_store))
            self.tool_registry.register(MemorySearchTool(self.config, self.memory_store))
            logger.info("✓ Memory System enabled (DB: %s)", self.config.memory_db_path)
        else:
            logger.info("○ Memory System disabled")

        # === 4. Skill System ===
        self.skill_catalog = SkillCatalog()
        self.skill_catalog.register_all_builtin()
        self.skill_version_store = SkillVersionStore(self.config.skill_eval_db_path)
        for skill in self.skill_catalog.get_all():
            self.skill_version_store.ensure_active(
                skill.name,
                skill.meta.model_dump_json(),
            )
        self.skill_router = SkillRouter(
            self.skill_catalog,
            evidence_store=self.skill_version_store,
        )
        logger.info("✓ Skill System: %d skills loaded", self.skill_catalog.skill_count)

        # === 5. Context Manager ===
        self.context_manager = ContextManager(self.config)
        logger.info("✓ Context Manager initialized")

        # === 6. Multi-Agent Orchestrator ===
        self.orchestrator = Orchestrator(self.config, self.llm_client, self.tool_registry)
        self.orchestrator.register_agent_tool()
        logger.info("✓ Multi-Agent Orchestrator initialized")

        # === 7. Security ===
        self.auditor = SecurityAuditor()
        self.permission_manager = PermissionManager(self.config)
        logger.info("✓ Security layer initialized")

        # === 8. Conversation Context + durable runtime ===
        self.context = ConversationContext(self.config)
        self.event_store = SQLiteEventStore(self.config.event_db_path)
        self.event_store.append(
            session_id=self.context.session_id,
            event_type=EventType.SESSION_STARTED,
            payload={"model": self.config.model},
        )
        self.checkpoint_manager = CheckpointManager(
            self.event_store,
            self.change_engine,
            self.context,
        )
        from miniclaude.tools.checkpoint_tools import (
            ListPromptCheckpointsTool,
            RewindCheckpointTool,
        )
        self.tool_registry.register(ListPromptCheckpointsTool(self.checkpoint_manager))
        self.tool_registry.register(RewindCheckpointTool(self.checkpoint_manager))

        # === 9. Lifecycle hooks + Tool Executor ===
        self.dispatcher = LifecycleDispatcher()
        self.hook_bus = HookBus(self.event_store)
        self.dispatcher.subscribe(self.hook_bus.observe_event, name="hook-runtime-observer")
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            self.config,
            permission_manager=self.permission_manager,
            auditor=self.auditor,
            context_manager=self.context_manager,
            hook_bus=self.hook_bus,
        )

        # === 10. Query Loop (core engine) ===
        self.query_loop = QueryLoop(
            llm_client=self.llm_client,
            tool_executor=self.tool_executor,
            context=self.context,
            config=self.config,
            event_store=self.event_store,
            dispatcher=self.dispatcher,
            skill_router=self.skill_router,
        )

        # === 11. REPL ===
        self.repl = REPL(self.query_loop, self.context, self.checkpoint_manager)

        self._initialized = True
        if self.config.transaction_auto_prune and self.config.transaction_prune_interval_hours > 0:
            self._retention_task = asyncio.create_task(self._run_retention_loop())
        total_tools = len(self.tool_registry)
        logger.info("═" * 50)
        logger.info("MiniClaude initialized successfully!")
        logger.info("  Tools: %d | Skills: %d | Memory: %s",
                    total_tools,
                    self.skill_catalog.skill_count,
                    "ON" if self.config.memory_enabled else "OFF")
        logger.info("═" * 50)

    async def run_query(self, query: str) -> str:
        """Run a single query and return the response."""
        if not self._initialized:
            await self.initialize()
        assert self.query_loop is not None
        return await self.query_loop.run(query)

    def start_new_session(self) -> str:
        if self.context is None or self.event_store is None:
            raise RuntimeError("Application is not initialized")
        self.context.clear()
        self.event_store.append(
            session_id=self.context.session_id,
            event_type=EventType.SESSION_STARTED,
            payload={"model": self.config.model},
        )
        return self.context.session_id

    def switch_session(self, session_id: str) -> str:
        if self.context is None or self.event_store is None:
            raise RuntimeError("Application is not initialized")
        if session_id not in self.event_store.list_sessions():
            raise ValueError(f"Unknown session: {session_id}")
        self.context.session_id = session_id
        self.context.messages = project_messages(self.event_store.list_events(session_id))
        return session_id

    async def run_repl(self) -> None:
        """Start the interactive REPL."""
        if not self._initialized:
            await self.initialize()
        assert self.repl is not None
        await self.repl.run()

    async def shutdown(self) -> None:
        """Clean up all resources."""
        logger.info("Shutting down MiniClaude ...")

        if self._retention_task:
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass
            self._retention_task = None

        # Close memory store
        if self.memory_store:
            self.memory_store.close()

        if self.event_store:
            self.event_store.close()

        if self.skill_version_store:
            self.skill_version_store.close()

        if self.causal_ledger:
            self.causal_ledger.close()
            self.causal_ledger = None
            self.causal_coordinator = None

        # Close LLM client
        if self.llm_client:
            await self.llm_client.close()

        logger.info("Shutdown complete.")

    async def _run_retention_loop(self) -> None:
        interval = self.config.transaction_prune_interval_hours * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                report = await asyncio.to_thread(self.change_engine.prune, dry_run=False)
                if report.eligible_transactions or report.released_bytes:
                    logger.info(
                        "Periodic transaction retention expired %d receipts and released %d bytes",
                        len(report.eligible_transactions), report.released_bytes,
                    )
            except Exception:
                logger.exception("Periodic transaction retention failed")
