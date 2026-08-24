"""Orchestrator - main agent's multi-agent coordination hub."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from undomesh.agents.contracts import ContractResult, TaskContract, TaskStatus
from undomesh.agents.scheduler import ContractScheduler
from undomesh.agents.sub_agent import AgentResult, SubAgent, AGENT_PROFILES
from undomesh.core.config import UndoMeshConfig
from undomesh.llm.client import ModelClient
from undomesh.tools.base import BaseTool, ToolResult
from undomesh.tools.registry import ToolRegistry
from undomesh.runtime.store import EventStore

logger = logging.getLogger(__name__)


class AgentTool(BaseTool):
    """Tool that spawns a sub-agent for delegated tasks.

    The main agent can call this tool to delegate a subtask
    to a specialized agent with its own constrained tool set.
    """

    def __init__(
        self,
        config: UndoMeshConfig,
        llm_client: ModelClient,
        tool_registry: ToolRegistry,
    ) -> None:
        self._config = config
        self._llm_client = llm_client
        self._tool_registry = tool_registry

    @property
    def name(self) -> str:
        return "spawn_agent"

    @property
    def description(self) -> str:
        agent_types = "\n".join(
            f"  - {k}: {v['description']}" for k, v in AGENT_PROFILES.items()
        )
        return (
            "Spawn a specialized sub-agent to handle a subtask independently. "
            "The sub-agent has its own context and restricted tool access. "
            "Use this for complex tasks that benefit from focused execution.\n\n"
            f"Available agent types:\n{agent_types}"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "task": {
                "type": "string",
                "description": "Detailed task description for the sub-agent",
            },
            "agent_type": {
                "type": "string",
                "enum": list(AGENT_PROFILES.keys()),
                "description": "Type of specialist agent to spawn",
            },
            "context": {
                "type": "string",
                "description": "Additional context (file paths, constraints, etc.)",
            },
        }

    def _get_required_params(self) -> list[str]:
        return ["task", "agent_type"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        task = kwargs.get("task", "")
        agent_type = kwargs.get("agent_type", "")
        context_str = kwargs.get("context", "")

        if not task:
            return self._error("Empty task")
        if agent_type not in AGENT_PROFILES:
            return self._error(f"Unknown agent type: {agent_type}")

        try:
            agent = SubAgent(
                agent_type=agent_type,
                config=self._config,
                llm_client=self._llm_client,
                tool_registry=self._tool_registry,
            )

            context = {"additional": context_str} if context_str else None
            result = await agent.run(task, context)

            # Convert to tool result
            tool_result = result.to_tool_result()
            logger.info(
                "Agent [%s] completed: success=%s, tools=%d",
                agent_type, result.success, len(result.tools_called),
            )
            return tool_result

        except Exception as e:
            logger.error("Agent spawn failed: %s", e, exc_info=True)
            return self._error(f"Failed to spawn agent: {e}")


class Orchestrator:
    """Main agent's orchestration layer for multi-agent coordination.

    Manages:
    - Agent spawning and lifecycle
    - Task decomposition planning
    - Result aggregation
    - Quality control
    """

    def __init__(
        self,
        config: UndoMeshConfig,
        llm_client: ModelClient,
        tool_registry: ToolRegistry,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self._active_agents: dict[str, SubAgent] = {}
        self._completed_results: list[AgentResult] = []

    def register_agent_tool(self) -> None:
        """Register the spawn_agent tool in the tool registry."""
        agent_tool = AgentTool(self.config, self.llm_client, self.tool_registry)
        self.tool_registry.register(agent_tool)
        logger.info("Registered agent spawning tool")

    async def spawn_agent(
        self,
        agent_type: str,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Spawn a sub-agent for a specific task."""
        agent = SubAgent(
            agent_type=agent_type,
            config=self.config,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
        )

        self._active_agents[agent.name] = agent

        try:
            result = await agent.run(task, context)
            self._completed_results.append(result)
            return result
        finally:
            self._active_agents.pop(agent.name, None)

    async def execute_contracts(
        self,
        contracts: list[TaskContract],
        *,
        event_store: EventStore | None = None,
        session_id: str = "orchestrator",
        max_concurrency: int = 4,
        isolate_writes: bool = True,
    ) -> dict[str, ContractResult]:
        """Execute a dependency graph with explicit budgets and write sets."""

        async def runner(contract: TaskContract, workspace: Path) -> ContractResult:
            scoped_config = self.config.model_copy(
                update={"project_dir": str(workspace)}
            )
            scoped_registry = ToolRegistry(scoped_config)
            scoped_registry.register_all()
            agent = SubAgent(
                agent_type=contract.agent_type,
                config=scoped_config,
                llm_client=self.llm_client,
                tool_registry=scoped_registry,
            )
            context = {
                "task_id": contract.task_id,
                "parent_task_id": contract.parent_task_id,
                "session_id": session_id,
                "worktree_id": workspace.name,
                "readable_paths": contract.readable_paths,
                "writable_paths": contract.writable_paths,
                "dependencies": contract.dependencies,
                "expected_outputs": contract.expected_outputs,
                "verification_commands": contract.verification_commands,
            }
            result = await agent.run(contract.objective, context)
            return ContractResult(
                task_id=contract.task_id,
                status=TaskStatus.COMPLETED if result.success else TaskStatus.FAILED,
                output=result.output,
                error=result.error,
                iterations=result.iterations,
                tokens=result.total_input_tokens + result.total_output_tokens,
                workspace=str(workspace),
            )

        scheduler = ContractScheduler(
            self.config.effective_project_dir(),
            runner,
            max_concurrency=max_concurrency,
            isolate_writes=isolate_writes,
            event_store=event_store,
            session_id=session_id,
        )
        return await scheduler.execute(contracts)

    @property
    def active_agent_count(self) -> int:
        return len(self._active_agents)

    @property
    def total_agents_spawned(self) -> int:
        return len(self._completed_results)
