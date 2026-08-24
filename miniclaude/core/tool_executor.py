"""Tool execution engine - runs tool calls and collects results."""

from __future__ import annotations

import logging
import time
import asyncio
from dataclasses import dataclass, field
from typing import Any

from miniclaude.tools.base import BaseTool, ToolResult
from miniclaude.tools.registry import ToolRegistry
from miniclaude.context.manager import ContextManager
from miniclaude.execution.capabilities import CapabilitySet, ToolExecutionContext
from miniclaude.execution.paths import PathBoundaryError, resolve_within_roots
from miniclaude.security.auditor import SecurityAuditor
from miniclaude.security.permission import PermissionManager
from miniclaude.provenance.policy import PolicyAction, ProvenancePolicy
from miniclaude.execution.validation import validate_tool_input
from miniclaude.runtime.hooks import HookBus, HookContext, HookPoint, HookVerdict

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing a batch of tool calls."""

    results: list[ToolResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_time_ms: float = 0.0

    @property
    def all_success(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def has_results(self) -> bool:
        return len(self.results) > 0


class ToolExecutor:
    """Executes tool calls from the LLM response.

    Handles:
    - Dispatching tool calls to the correct tool implementation
    - Error handling and timeouts
    - Execution timing and logging
    """

    def __init__(
        self,
        registry: ToolRegistry,
        config: Any = None,
        *,
        permission_manager: PermissionManager | None = None,
        auditor: SecurityAuditor | None = None,
        context_manager: ContextManager | None = None,
        default_capabilities: CapabilitySet | None = None,
        provenance_policy: ProvenancePolicy | None = None,
        hook_bus: HookBus | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or registry.config
        self.permission_manager = permission_manager
        self.auditor = auditor
        self.context_manager = context_manager
        self.default_capabilities = default_capabilities or CapabilitySet.for_project(
            self.config.effective_project_dir(),
            set(registry.get_names()),
            shell_enabled=self.config.allow_shell,
        )
        self.provenance_policy = provenance_policy or ProvenancePolicy()
        self.hook_bus = hook_bus

    def approve_registered_tools(self, names: set[str]) -> None:
        """Explicitly grant newly registered tools without widening other rights."""
        unknown = names.difference(self.registry.get_names())
        if unknown:
            raise ValueError(f"cannot approve unregistered tools: {sorted(unknown)}")
        current = self.default_capabilities
        self.default_capabilities = CapabilitySet(
            allowed_tools=current.allowed_tools.union(names),
            readable_roots=current.readable_roots,
            writable_roots=current.writable_roots,
            network_enabled=current.network_enabled,
            shell_enabled=current.shell_enabled,
            max_processes=current.max_processes,
        )

    async def execute_all(
        self,
        tool_calls: list[dict[str, Any]],
        execution_context: ToolExecutionContext | None = None,
    ) -> list[ToolResult]:
        """Execute all tool calls from an LLM response.

        Args:
            tool_calls: List of tool call dicts with 'id', 'name', 'input' keys.

        Returns:
            List of ToolResult, one per tool call.
        """
        results: list[ToolResult] = []

        for tc in tool_calls:
            call_id = tc.get("id", "unknown")
            name = tc.get("name", "")
            params = tc.get("input", {})

            result = await self.execute_single(name, params, execution_context)
            result.metadata["call_id"] = call_id
            results.append(result)

        return results

    async def execute_single(
        self,
        name: str,
        params: dict[str, Any],
        execution_context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """Execute a single tool call by name.

        Args:
            name: Tool name.
            params: Tool parameters.

        Returns:
            ToolResult from the tool execution.
        """
        context = execution_context or ToolExecutionContext(
            capability_set=self.default_capabilities,
        )
        capabilities = context.capability_set or self.default_capabilities

        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(
                tool_name=name,
                success=False,
                content=f"Unknown tool: '{name}'. Available: {', '.join(self.registry.get_names())}",
            )

        validation_errors = validate_tool_input(tool, params)
        if validation_errors:
            return ToolResult(
                tool_name=name,
                success=False,
                content="Invalid tool input: " + "; ".join(validation_errors),
            )

        if name not in capabilities.allowed_tools:
            return self._denied(name, params, "Tool is outside the granted capability set")
        if name == "run_command" and not capabilities.shell_enabled:
            return self._denied(name, params, "Shell execution is not granted")

        path_error = self._validate_paths(name, params, capabilities)
        if path_error:
            return self._denied(name, params, path_error)

        if self.hook_bus:
            hook_outcome = await self.hook_bus.run(HookContext(
                point=HookPoint.PRE_TOOL,
                session_id=context.session_id,
                turn_id=context.turn_id,
                step_id=context.step_id,
                agent_id=context.agent_id,
                tool_name=name,
                params=dict(params),
            ))
            if hook_outcome.verdict == HookVerdict.DENY:
                return self._denied(name, params, hook_outcome.reason or "Denied by lifecycle hook")
            if hook_outcome.verdict == HookVerdict.ASK:
                approved = bool(
                    self.permission_manager
                    and self.permission_manager.request_confirmation(
                        name, params, hook_outcome.reason or "Lifecycle hook requested approval"
                    )
                )
                if not approved:
                    return self._denied(name, params, hook_outcome.reason or "Hook approval required")

        provenance_decision = self.provenance_policy.evaluate(
            name,
            params,
            context.provenance,
        )
        if provenance_decision.action == PolicyAction.DENY:
            return self._denied(name, params, provenance_decision.reason)
        if provenance_decision.action == PolicyAction.ASK:
            approved = bool(
                self.permission_manager
                and self.permission_manager.request_confirmation(
                    name,
                    params,
                    provenance_decision.reason,
                )
            )
            if not approved:
                return self._denied(
                    name,
                    params,
                    f"Confirmation required: {provenance_decision.reason}",
                )

        if self.permission_manager:
            permission = await self.permission_manager.check(
                name,
                params,
                user_input=context.user_input,
            )
            if not permission.allowed:
                return self._denied(name, params, permission.message)

        start_time = time.monotonic()

        try:
            logger.info("Executing tool: %s(%s)", name, _format_params(params))
            result = await asyncio.wait_for(
                tool.execute(**params),
                timeout=self.config.tool_timeout_seconds,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000
            result.metadata["execution_time_ms"] = round(elapsed_ms, 1)

            logger.info(
                "Tool %s: %s (%.0fms, %d chars)",
                name,
                "success" if result.success else "error",
                elapsed_ms,
                len(result.content),
            )
            if self.context_manager:
                result = self.context_manager.process_tool_result(result)
            if self.auditor:
                self.auditor.log_tool_call(name, params, result.success, result.content[:200])
            if self.hook_bus:
                await self.hook_bus.run(HookContext(
                    point=HookPoint.POST_TOOL if result.success else HookPoint.TOOL_FAILURE,
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    step_id=context.step_id,
                    agent_id=context.agent_id,
                    tool_name=name,
                    params=dict(params),
                    result=result,
                ))
            return result

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            result = ToolResult(
                tool_name=name,
                success=False,
                content=f"Tool execution timed out after {self.config.tool_timeout_seconds}s",
                metadata={"execution_time_ms": round(elapsed_ms, 1)},
            )
            if self.auditor:
                self.auditor.log_tool_call(name, params, False, result.content, risk_level="medium")
            if self.hook_bus:
                await self.hook_bus.run(HookContext(
                    point=HookPoint.TOOL_FAILURE, session_id=context.session_id,
                    turn_id=context.turn_id, step_id=context.step_id,
                    agent_id=context.agent_id, tool_name=name, params=dict(params), result=result,
                ))
            return result
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error("Tool %s crashed: %s (%.0fms)", name, e, elapsed_ms)
            result = ToolResult(
                tool_name=name,
                success=False,
                content=f"Tool execution failed: {type(e).__name__}: {e}",
                metadata={"execution_time_ms": round(elapsed_ms, 1)},
            )
            if self.auditor:
                self.auditor.log_tool_call(name, params, False, result.content, risk_level="medium")
            if self.hook_bus:
                await self.hook_bus.run(HookContext(
                    point=HookPoint.TOOL_FAILURE, session_id=context.session_id,
                    turn_id=context.turn_id, step_id=context.step_id,
                    agent_id=context.agent_id, tool_name=name, params=dict(params), result=result,
                ))
            return result

    def _validate_paths(
        self,
        name: str,
        params: dict[str, Any],
        capabilities: CapabilitySet,
    ) -> str | None:
        if name == "apply_patch_set":
            try:
                from miniclaude.execution.patches import PatchSet
                patch = PatchSet.parse(str(params.get("patch", "")))
                for relative in patch.touched_paths:
                    resolve_within_roots(
                        relative,
                        base=self.config.effective_project_dir(),
                        roots=capabilities.writable_roots,
                    )
                return None
            except (PathBoundaryError, ValueError) as exc:
                return str(exc)
        path_key = "cwd" if name == "run_command" else "path"
        raw_path = params.get(path_key)
        if name == "run_command" and not raw_path:
            raw_path = str(self.config.effective_project_dir())
        if not raw_path:
            return None

        write_tools = {"write_file", "edit_file", "apply_patch_set"}
        roots = capabilities.writable_roots if name in write_tools else capabilities.readable_roots
        try:
            resolved = resolve_within_roots(
                str(raw_path),
                base=self.config.effective_project_dir(),
                roots=roots,
            )
            params[path_key] = str(resolved)
        except PathBoundaryError as exc:
            return str(exc)
        return None

    def _denied(self, name: str, params: dict[str, Any], reason: str) -> ToolResult:
        if self.auditor:
            self.auditor.log_tool_call(name, params, False, reason, risk_level="high")
        return ToolResult(
            tool_name=name,
            success=False,
            content=f"Permission denied: {reason}",
            metadata={"denied": True, "denial_reason": reason},
        )


def _format_params(params: dict[str, Any], max_len: int = 100) -> str:
    """Format parameters for logging (truncate long values)."""
    parts = []
    for k, v in params.items():
        val_str = str(v)
        if len(val_str) > 50:
            val_str = val_str[:50] + "..."
        parts.append(f"{k}={val_str}")
    result = ", ".join(parts)
    if len(result) > max_len:
        result = result[:max_len] + "..."
    return result
