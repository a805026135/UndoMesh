"""Offline integration tests for the QueryLoop runtime lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import miniclaude
import miniclaude.core.loop as loop_module
from miniclaude.core.context import ConversationContext
from miniclaude.core.loop import QueryLoop
from miniclaude.core.tool_executor import ToolExecutor
from miniclaude.llm.client import LLMResponse
from miniclaude.runtime.events import EventType
from miniclaude.runtime.invariants import validate_lifecycle
from miniclaude.runtime.store import InMemoryEventStore
from miniclaude.tools.base import BaseTool, ToolResult
from miniclaude.tools.registry import ToolRegistry
from conftest import ScriptedLLMClient


class EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Return the supplied text."

    @property
    def parameters(self) -> dict:
        return {"text": {"type": "string"}}

    def _get_required_params(self) -> list[str]:
        return ["text"]

    async def execute(self, **kwargs) -> ToolResult:
        return self._success(str(kwargs["text"]))


def make_loop(config, responses, event_store=None):
    registry = ToolRegistry(config)
    registry.register(EchoTool())
    client = ScriptedLLMClient(responses)
    context = ConversationContext(config)
    store = event_store or InMemoryEventStore()
    query_loop = QueryLoop(
        llm_client=client,  # type: ignore[arg-type]
        tool_executor=ToolExecutor(registry, config),
        context=context,
        config=config,
        event_store=store,
    )
    return query_loop, client, context


def event_types(query_loop: QueryLoop) -> list[EventType]:
    return [event.event_type for event in query_loop.events]


def test_tests_import_current_checkout():
    repository = Path(__file__).resolve().parents[1]
    imported = Path(miniclaude.__file__).resolve()
    assert imported.is_relative_to(repository)


@pytest.mark.asyncio
async def test_text_response_emits_closed_lifecycle(config):
    query_loop, client, context = make_loop(
        config,
        [LLMResponse(text="done", stop_reason="stop")],
    )

    result = await query_loop.run("hello")

    assert result == "done"
    assert len(client.requests) == 1
    assert context.messages[-1] == {"role": "assistant", "content": "done"}
    assert event_types(query_loop) == [
        EventType.TURN_STARTED,
        EventType.STEP_STARTED,
        EventType.MODEL_REQUESTED,
        EventType.MODEL_RESPONDED,
        EventType.STEP_ENDED,
        EventType.TURN_ENDED,
    ]
    assert [event.sequence for event in query_loop.events] == list(range(1, 7))
    assert len({event.turn_id for event in query_loop.events}) == 1
    validate_lifecycle(query_loop.events)


@pytest.mark.asyncio
async def test_tool_call_then_text_emits_two_closed_steps(config):
    query_loop, client, context = make_loop(
        config,
        [
            LLMResponse(
                tool_calls=[{"id": "call-1", "name": "echo", "input": {"text": "hello"}}],
                stop_reason="tool_calls",
            ),
            LLMResponse(text="echoed", stop_reason="stop"),
        ],
    )

    result = await query_loop.run("use a tool")

    assert result == "echoed"
    assert len(client.requests) == 2
    assert any(message.get("role") == "tool" and message["content"] == "hello" for message in context.messages)
    types = event_types(query_loop)
    assert types.count(EventType.STEP_STARTED) == 2
    assert types.count(EventType.STEP_ENDED) == 2
    assert EventType.TOOL_REQUESTED in types
    assert EventType.TOOL_STARTED in types
    assert EventType.TOOL_COMPLETED in types
    assert types[-1] == EventType.TURN_ENDED
    tool_events = [event for event in query_loop.events if event.event_type.value.startswith("tool.")]
    assert {event.payload["call_id"] for event in tool_events} == {"call-1"}
    validate_lifecycle(query_loop.events)


@pytest.mark.asyncio
async def test_multiple_tool_calls_share_one_step_and_keep_call_ids(config):
    query_loop, _, context = make_loop(
        config,
        [
            LLMResponse(
                tool_calls=[
                    {"id": "call-a", "name": "echo", "input": {"text": "A"}},
                    {"id": "call-b", "name": "echo", "input": {"text": "B"}},
                ],
                stop_reason="tool_calls",
            ),
            LLMResponse(text="both complete", stop_reason="stop"),
        ],
    )

    assert await query_loop.run("run both") == "both complete"
    first_step_id = next(
        event.step_id for event in query_loop.events if event.event_type == EventType.STEP_STARTED
    )
    tool_events = [event for event in query_loop.events if event.event_type.value.startswith("tool.")]
    assert {event.payload["call_id"] for event in tool_events} == {"call-a", "call-b"}
    assert {event.step_id for event in tool_events} == {first_step_id}
    tool_messages = [message for message in context.messages if message.get("role") == "tool"]
    assert [(message["tool_call_id"], message["content"]) for message in tool_messages] == [
        ("call-a", "A"),
        ("call-b", "B"),
    ]


@pytest.mark.asyncio
async def test_unknown_tool_is_recorded_as_failed_and_loop_continues(config):
    query_loop, _, context = make_loop(
        config,
        [
            LLMResponse(
                tool_calls=[{"id": "bad-1", "name": "missing", "input": {}}],
                stop_reason="tool_calls",
            ),
            LLMResponse(text="recovered", stop_reason="stop"),
        ],
    )

    assert await query_loop.run("call missing") == "recovered"
    assert EventType.TOOL_FAILED in event_types(query_loop)
    assert any(
        message.get("role") == "tool" and "Unknown tool" in message["content"]
        for message in context.messages
    )


@pytest.mark.asyncio
async def test_model_failure_closes_step_and_turn(config):
    query_loop, _, _ = make_loop(config, [RuntimeError("provider unavailable")])

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await query_loop.run("fail")

    assert event_types(query_loop)[-3:] == [
        EventType.MODEL_FAILED,
        EventType.STEP_FAILED,
        EventType.TURN_FAILED,
    ]


@pytest.mark.asyncio
async def test_context_overflow_compacts_event_history_and_retries(config):
    config.context_limit = 400
    query_loop, client, context = make_loop(
        config,
        [
            LLMResponse(text="A" * 3000, stop_reason="stop"),
            LLMResponse(text="B" * 3000, stop_reason="stop"),
            RuntimeError("maximum context window exceeded"),
            LLMResponse(text="recovered", stop_reason="stop"),
        ],
    )
    await query_loop.run("first")
    await query_loop.run("second")

    assert await query_loop.run("third") == "recovered"
    assert len(client.requests) == 4
    assert EventType.CONTEXT_COMPACTED in event_types(query_loop)
    assert any(
        message.get("content", "").startswith("[Structured context summary]")
        for message in context.messages
    )


@pytest.mark.asyncio
async def test_cancellation_closes_step_and_turn(config):
    query_loop, _, _ = make_loop(config, [asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await query_loop.run("cancel")

    assert event_types(query_loop)[-2:] == [
        EventType.STEP_FAILED,
        EventType.TURN_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_max_iterations_closes_turn(config, monkeypatch):
    monkeypatch.setattr(loop_module, "MAX_ITERATIONS", 2)
    query_loop, _, _ = make_loop(
        config,
        [
            LLMResponse(
                tool_calls=[{"id": "loop-1", "name": "echo", "input": {"text": "again"}}],
                stop_reason="tool_calls",
            ),
            LLMResponse(
                tool_calls=[{"id": "loop-2", "name": "echo", "input": {"text": "again"}}],
                stop_reason="tool_calls",
            ),
        ],
    )

    result = await query_loop.run("keep going")

    assert "maximum number of tool call iterations (2)" in result
    assert query_loop.events[-1].event_type == EventType.TURN_ENDED
    assert query_loop.events[-1].payload["outcome"] == "max_iterations"
    assert event_types(query_loop).count(EventType.STEP_ENDED) == 2
    validate_lifecycle(query_loop.events)
