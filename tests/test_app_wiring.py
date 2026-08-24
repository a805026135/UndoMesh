"""Composition test proving runtime subsystems are connected in the real app."""

from __future__ import annotations

import pytest

from undomesh.app import UndoMeshApp
from undomesh.runtime.store import SQLiteEventStore


@pytest.mark.asyncio
async def test_app_wires_security_context_and_durable_events(config, tmp_path):
    config.event_db = str(tmp_path / "runtime.db")
    config.skill_eval_db = str(tmp_path / "skills.db")
    app = UndoMeshApp(config)

    await app.initialize()

    assert isinstance(app.event_store, SQLiteEventStore)
    assert app.tool_executor.permission_manager is app.permission_manager
    assert app.tool_executor.context_manager is app.context_manager
    assert app.tool_executor.auditor is app.auditor
    assert app.query_loop.event_store is app.event_store
    assert app.query_loop.skill_router is app.skill_router
    assert app.checkpoint_manager is not None
    assert {"list_prompt_checkpoints", "rewind_checkpoint"} <= set(app.tool_registry.get_names())
    assert "rewind_checkpoint" in app.tool_executor.default_capabilities.allowed_tools
    assert app.tool_executor.hook_bus is app.hook_bus
    assert app.event_store.list_events(app.context.session_id)[0].event_type.value == "session.started"

    first_session = app.context.session_id
    second_session = app.start_new_session()
    assert second_session != first_session
    assert app.event_store.list_events(second_session)[0].event_type.value == "session.started"
    assert app.switch_session(first_session) == first_session
    assert app.context.session_id == first_session

    await app.shutdown()
