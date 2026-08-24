"""Query Loop - the core agent execution loop.

This is the heart of UndoMesh: User Query → LLM → Tool Calls → Results → Loop
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from uuid import uuid4

from undomesh.core.config import UndoMeshConfig
from undomesh.core.context import ConversationContext
from undomesh.core.tool_executor import ToolExecutor
from undomesh.llm.client import ModelClient, LLMResponse
from undomesh.llm.message_builder import MessageBuilder
from undomesh.execution.capabilities import ToolExecutionContext
from undomesh.runtime.events import AgentEvent, EventType
from undomesh.runtime.dispatcher import LifecycleDispatcher
from undomesh.runtime.store import EventStore, InMemoryEventStore
from undomesh.provenance.models import Provenance, SourceType, TrustLevel
from undomesh.context.event_compactor import EventCompactor
from undomesh.runtime.projection import project_messages
from undomesh.skills.base import SkillContext
from undomesh.skills.router import SkillRouter

logger = logging.getLogger(__name__)

# Maximum tool-call iterations before forcing a text response
MAX_ITERATIONS = 50


class QueryLoop:
    """Core agent loop: Query → LLM Reasoning → Tool Execution → Loop.

    Flow:
    1. User input → build messages
    2. Send to LLM with tool definitions
    3. If LLM returns tool_calls → execute tools → feed results back → go to 2
    4. If LLM returns text only → done, return to user
    """

    def __init__(
        self,
        llm_client: ModelClient,
        tool_executor: ToolExecutor,
        context: ConversationContext,
        config: UndoMeshConfig,
        event_store: EventStore | None = None,
        dispatcher: LifecycleDispatcher | None = None,
        skill_router: SkillRouter | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.context = context
        self.config = config
        self.event_store = event_store or InMemoryEventStore()
        self.dispatcher = dispatcher or LifecycleDispatcher()
        self.event_compactor = EventCompactor(self.event_store)
        self.skill_router = skill_router
        self._on_tool_start: Callable[[str, dict], None] | None = None
        self._on_tool_end: Callable[[str, bool, str], None] | None = None
        self._on_thinking: Callable[[str], None] | None = None
        self._iteration_count = 0
        self._active_turn_id: str | None = None

    def set_callbacks(
        self,
        on_tool_start: Callable[[str, dict], None] | None = None,
        on_tool_end: Callable[[str, bool, str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
    ) -> None:
        """Set UI callback functions for progress reporting."""
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._on_thinking = on_thinking

    async def run(self, user_input: str) -> str:
        """Run the agent loop for a single user query.

        Args:
            user_input: The user's text input/query.

        Returns:
            Final text response from the LLM.
        """
        logger.info("=== New Query ===")
        logger.info("User: %s", user_input[:200])

        # Add user message to context
        self.context.add_user_message(user_input)
        self._iteration_count = 0
        turn_id = str(uuid4())
        user_provenance = Provenance(
            source_type=SourceType.USER_INPUT,
            trust_level=TrustLevel.TRUSTED_USER,
            source_id=f"user:{turn_id}",
        )
        active_provenance: list[Provenance] = [user_provenance]
        self._active_turn_id = turn_id
        self._emit(
            EventType.TURN_STARTED,
            turn_id=turn_id,
            payload={"input": user_input, "input_length": len(user_input)},
            provenance=user_provenance,
        )

        skill_context_text = ""
        if self.skill_router:
            selected_skills = await self.skill_router.route(user_input)
            self._emit(
                EventType.SKILL_ROUTED,
                turn_id=turn_id,
                payload={"skills": [skill.name for skill in selected_skills]},
                provenance=user_provenance,
            )
            skill_sections: list[str] = []
            for skill in selected_skills:
                result = await self.skill_router.execute_skill(
                    skill,
                    SkillContext(
                        user_query=user_input,
                        conversation_history=self.context.get_messages(),
                    ),
                )
                self._emit(
                    EventType.SKILL_EXECUTED,
                    turn_id=turn_id,
                    payload={
                        "skill_name": skill.name,
                        "success": result.success,
                        "suggested_tools": result.suggested_tools,
                    },
                    provenance=Provenance(
                        source_type=SourceType.SKILL,
                        trust_level=TrustLevel.GENERATED,
                        source_id=f"skill:{skill.name}:{turn_id}",
                        parent_source_ids=(user_provenance.source_id,),
                    ),
                )
                if result.success and result.instructions:
                    skill_sections.append(f"### {skill.name}\n{result.instructions}")
            skill_context_text = "\n\n".join(skill_sections)
        self.context.build_system_prompt(skill_context=skill_context_text)

        # Build tool schemas (in Anthropic format, client will convert to OpenAI)
        tools_schema = self.tool_executor.registry.get_anthropic_schemas()

        # Main loop
        final_text = ""
        response: LLMResponse | None = None

        while self._iteration_count < MAX_ITERATIONS:
            self._iteration_count += 1
            step_id = str(uuid4())
            self._emit(
                EventType.STEP_STARTED,
                turn_id=turn_id,
                step_id=step_id,
                payload={"iteration": self._iteration_count},
            )
            logger.debug("Iteration %d/%d", self._iteration_count, MAX_ITERATIONS)

            # Get messages from context
            messages = self.context.get_messages()
            if self.tool_executor.context_manager:
                manager = self.tool_executor.context_manager
                manager.budget.update_system_tokens(self.context.system_prompt)
                manager.budget.update_tool_schema_tokens(tools_schema)
                manager.budget.update_history_tokens(messages)
                compacted = None
                if manager.budget.needs_compression():
                    compacted = self.event_compactor.compact_if_needed(
                        self.context.session_id,
                        target_tokens=max(256, int(manager.budget.available_for_history * 0.7)),
                    )
                compressed = (
                    project_messages(self.event_store.list_events(self.context.session_id))
                    if compacted
                    else manager.check_and_compress(
                        messages,
                        self.context.system_prompt,
                        tools_schema,
                    )
                )
                if compressed != messages:
                    original_count = len(messages)
                    self.context.messages = list(compressed)
                    messages = compressed
                    if not compacted:
                        self._emit(
                            EventType.CONTEXT_COMPACTED,
                            turn_id=turn_id,
                            step_id=step_id,
                            payload={
                                "original_message_count": original_count,
                                "message_count": len(compressed),
                                "usage_ratio": manager.budget.usage_ratio,
                            },
                        )

            # Call LLM
            self._emit(
                EventType.MODEL_REQUESTED,
                turn_id=turn_id,
                step_id=step_id,
                payload={"message_count": len(messages), "tool_count": len(tools_schema)},
                provenance=Provenance(
                    source_type=SourceType.MODEL_OUTPUT,
                    trust_level=TrustLevel.GENERATED,
                    source_id=f"model-request:{step_id}",
                    parent_source_ids=tuple(item.source_id for item in active_provenance),
                ),
            )
            try:
                response = await self.llm_client.chat(
                    messages=messages,
                    system=self.context.system_prompt,
                    tools=tools_schema,
                )
            except asyncio.CancelledError:
                self._emit(
                    EventType.STEP_FAILED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"reason": "cancelled"},
                )
                self._emit(
                    EventType.TURN_CANCELLED,
                    turn_id=turn_id,
                    payload={"iteration": self._iteration_count},
                )
                self._active_turn_id = None
                raise
            except Exception as exc:
                error_payload = {"error_type": type(exc).__name__, "message": str(exc)}
                self._emit(
                    EventType.MODEL_FAILED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload=error_payload,
                )
                self._emit(
                    EventType.STEP_FAILED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload=error_payload,
                )
                if self._is_context_overflow(exc):
                    compacted = self.event_compactor.compact_if_needed(
                        self.context.session_id,
                        target_tokens=max(
                            256,
                            int(self.config.context_limit * 0.6),
                        ),
                    )
                    if compacted:
                        self.context.messages = project_messages(
                            self.event_store.list_events(self.context.session_id)
                        )
                        logger.warning(
                            "Recovered from context overflow with compaction %s",
                            compacted.compaction_id,
                        )
                        continue
                self._emit(
                    EventType.TURN_FAILED,
                    turn_id=turn_id,
                    payload=error_payload,
                )
                self._active_turn_id = None
                raise

            model_provenance = Provenance(
                source_type=SourceType.MODEL_OUTPUT,
                trust_level=TrustLevel.GENERATED,
                source_id=f"model-response:{step_id}",
                parent_source_ids=tuple(item.source_id for item in active_provenance),
            )
            self._emit(
                EventType.MODEL_RESPONDED,
                turn_id=turn_id,
                step_id=step_id,
                payload={
                    "text_length": len(response.text),
                    "tool_call_count": len(response.tool_calls),
                    "stop_reason": response.stop_reason,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "text": response.text,
                    "tool_calls": response.tool_calls,
                },
                provenance=model_provenance,
            )

            # Report thinking text
            if response.text and self._on_thinking:
                self._on_thinking(response.text)

            # If no tool calls, we're done
            if not response.has_tool_use:
                final_text = response.text
                # Save assistant response to context
                self.context.add_assistant_message(response.text)
                self._emit(
                    EventType.STEP_ENDED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"outcome": "final_response"},
                )
                self._emit(
                    EventType.TURN_ENDED,
                    turn_id=turn_id,
                    payload={
                        "outcome": "completed",
                        "iterations": self._iteration_count,
                        "response_length": len(final_text),
                    },
                )
                self._active_turn_id = None
                logger.info(
                    "Agent finished in %d iterations. Response: %d chars",
                    self._iteration_count, len(final_text),
                )
                break

            # Has tool calls - build assistant message with tool_calls and add to context
            assistant_msg = MessageBuilder.build_assistant_with_tool_use(
                response.text, response.tool_calls
            )
            self.context.add_raw_message(assistant_msg)

            # Execute tool calls
            for tc in response.tool_calls:
                self._emit(
                    EventType.TOOL_REQUESTED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"call_id": tc.get("id", "unknown"), "tool_name": tc.get("name", "")},
                    provenance=model_provenance,
                )
                self._emit(
                    EventType.TOOL_STARTED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"call_id": tc.get("id", "unknown"), "tool_name": tc.get("name", "")},
                    provenance=model_provenance,
                )
                if self._on_tool_start:
                    self._on_tool_start(tc["name"], tc["input"])

            try:
                results = await self.tool_executor.execute_all(
                    response.tool_calls,
                    ToolExecutionContext(
                        session_id=self.context.session_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        agent_id="root",
                        user_input=user_input,
                        capability_set=self.tool_executor.default_capabilities,
                        provenance=tuple([*active_provenance, model_provenance]),
                    ),
                )
            except asyncio.CancelledError:
                for tc in response.tool_calls:
                    self._emit(
                        EventType.TOOL_FAILED,
                        turn_id=turn_id,
                        step_id=step_id,
                        payload={
                            "call_id": tc.get("id", "unknown"),
                            "tool_name": tc.get("name", ""),
                            "reason": "cancelled",
                        },
                    )
                self._emit(
                    EventType.STEP_FAILED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"reason": "cancelled"},
                )
                self._emit(
                    EventType.TURN_CANCELLED,
                    turn_id=turn_id,
                    payload={"iteration": self._iteration_count},
                )
                self._active_turn_id = None
                raise
            except Exception as exc:
                error_payload = {"error_type": type(exc).__name__, "message": str(exc)}
                for tc in response.tool_calls:
                    self._emit(
                        EventType.TOOL_FAILED,
                        turn_id=turn_id,
                        step_id=step_id,
                        payload={
                            "call_id": tc.get("id", "unknown"),
                            "tool_name": tc.get("name", ""),
                            **error_payload,
                        },
                    )
                self._emit(
                    EventType.STEP_FAILED,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload=error_payload,
                )
                self._emit(
                    EventType.TURN_FAILED,
                    turn_id=turn_id,
                    payload=error_payload,
                )
                self._active_turn_id = None
                raise

            for tc, result in zip(response.tool_calls, results):
                result_provenance = self._tool_result_provenance(tc, step_id, model_provenance)
                if result.success:
                    result_event_type = EventType.TOOL_COMPLETED
                elif result.metadata.get("denied"):
                    result_event_type = EventType.TOOL_DENIED
                else:
                    result_event_type = EventType.TOOL_FAILED
                self._emit(
                    result_event_type,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={
                        "call_id": tc.get("id", "unknown"),
                        "tool_name": tc.get("name", ""),
                        "result_length": len(result.content),
                        "content": result.content,
                        "success": result.success,
                        **(
                            {"transaction_id": result.metadata["transaction_id"]}
                            if result.metadata.get("transaction_id") else {}
                        ),
                    },
                    provenance=result_provenance,
                )
                active_provenance.append(result_provenance)
                if self._on_tool_end:
                    self._on_tool_end(
                        tc["name"],
                        result.success,
                        result.content[:200] if result.content else "",
                    )

            # Convert results to OpenAI message format and add to context
            tool_messages = MessageBuilder.tool_results_to_message(
                response.tool_calls, results
            )
            # tool_messages[0] is the assistant message with tool_calls
            # tool_messages[1:] are the tool result messages
            # We already added the assistant message, so just add the tool results
            for msg in tool_messages[1:]:  # Skip assistant msg, already added
                self.context.add_raw_message(msg)

            logger.debug(
                "Iteration %d: %d tool calls executed",
                self._iteration_count, len(results),
            )
            self._emit(
                EventType.STEP_ENDED,
                turn_id=turn_id,
                step_id=step_id,
                payload={"outcome": "tool_calls", "tool_call_count": len(results)},
            )

        else:
            # Hit max iterations
            final_text = (
                f"I've reached the maximum number of tool call iterations ({MAX_ITERATIONS}). "
                f"Here's what I've done so far:\n\n"
            )
            if response and response.text:
                final_text += response.text
            self.context.add_assistant_message(final_text)
            self._emit(
                EventType.TURN_ENDED,
                turn_id=turn_id,
                payload={"outcome": "max_iterations", "iterations": self._iteration_count},
            )
            self._active_turn_id = None
            logger.warning("Hit max iterations (%d)", MAX_ITERATIONS)

        return final_text

    @staticmethod
    def _is_context_overflow(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "context window",
                "context length",
                "maximum context",
                "too many tokens",
            )
        )

    def _emit(
        self,
        event_type: EventType,
        *,
        turn_id: str | None = None,
        step_id: str | None = None,
        payload: dict[str, Any] | None = None,
        provenance: Provenance | None = None,
    ) -> AgentEvent:
        """Append one lifecycle fact to the configured event store."""
        event = self.event_store.append(
            session_id=self.context.session_id,
            event_type=event_type,
            turn_id=turn_id,
            step_id=step_id,
            payload=payload,
            provenance=provenance,
        )
        self.dispatcher.publish(event)
        return event

    @staticmethod
    def _tool_result_provenance(
        tool_call: dict[str, Any],
        step_id: str,
        parent: Provenance,
    ) -> Provenance:
        name = tool_call.get("name", "unknown")
        call_id = tool_call.get("id", "unknown")
        if name in {"read_file", "grep_search", "glob_files"}:
            source_type = SourceType.WORKSPACE_FILE
            trust = TrustLevel.WORKSPACE
        elif name in {"web_fetch", "http_request"}:
            source_type = SourceType.WEB_CONTENT
            trust = TrustLevel.EXTERNAL
        elif name.startswith("mcp_"):
            source_type = SourceType.MCP_CONTENT
            trust = TrustLevel.EXTERNAL
        elif name == "spawn_agent":
            source_type = SourceType.SUBAGENT
            trust = TrustLevel.UNTRUSTED
        elif name in {"memory_search", "memory_save"}:
            source_type = SourceType.MEMORY
            trust = TrustLevel.UNTRUSTED
        else:
            source_type = SourceType.TOOL_RESULT
            trust = TrustLevel.GENERATED
        return Provenance(
            source_type=source_type,
            trust_level=trust,
            source_id=f"tool:{step_id}:{call_id}",
            parent_source_ids=(parent.source_id,),
            metadata=(("tool_name", name),),
        )

    @property
    def events(self) -> list[AgentEvent]:
        """Return the current session's runtime events."""
        return self.event_store.list_events(self.context.session_id)

    @property
    def iteration_count(self) -> int:
        return self._iteration_count

    @property
    def token_usage(self) -> dict[str, int]:
        """Get cumulative token usage."""
        return {
            "input_tokens": self.llm_client.total_input_tokens,
            "output_tokens": self.llm_client.total_output_tokens,
            "total_tokens": self.llm_client.total_tokens,
        }
