from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from miniclaude.agents.causal import CausalTaskLedger, CausalWorktreeCoordinator
from miniclaude.agents.contracts import TaskContract
from miniclaude.core.config import MiniClaudeConfig
from miniclaude.core.tool_executor import ToolExecutor
from miniclaude.execution.capabilities import CapabilitySet, ToolExecutionContext
from miniclaude.execution.transactions import DurableChangeEngine
from miniclaude.execution.worktrees import GitWorktreeManager
from miniclaude.tools.registry import ToolRegistry


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "causal@example.com")
    _git(path, "config", "user.name", "Causal Test")
    (path / "schema.txt").write_text("v1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "base")
    return path


def test_causal_closure_rolls_back_dependent_agent_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    ledger = CausalTaskLedger(tmp_path / "causal.db")
    coordinator = CausalWorktreeCoordinator(repo, tmp_path / "worktrees", ledger)

    schema = TaskContract(
        task_id="schema",
        agent_type="refactor_expert",
        objective="upgrade schema",
        writable_paths=("schema.txt",),
        readable_paths=("schema.txt",),
    )
    schema_worktree = coordinator.begin_task(schema)
    (schema_worktree.path / "schema.txt").write_text("v2\n", encoding="utf-8")
    schema_review = coordinator.review_task(
        schema,
        schema_worktree,
        verification_argv=["git", "status", "--short"],
        commit_message="agent: schema",
    )
    coordinator.approve_task(
        "schema", schema_review, approved_review_sha256=schema_review.review_sha256
    )

    client = TaskContract(
        task_id="client",
        agent_type="test_generator",
        parent_task_id="schema",
        objective="adapt client",
        dependencies=("schema",),
        writable_paths=("client.txt",),
        readable_paths=("schema.txt",),
    )
    client_worktree = coordinator.begin_task(client)
    (client_worktree.path / "client.txt").write_text("uses-v2\n", encoding="utf-8")
    client_review = coordinator.review_task(
        client,
        client_worktree,
        verification_argv=["git", "status", "--short"],
        commit_message="agent: client",
    )
    coordinator.approve_task(
        "client", client_review, approved_review_sha256=client_review.review_sha256
    )

    plan = ledger.plan_rollback("schema", strategy="cascade")
    assert plan.affected_task_ids == ("schema", "client")
    assert plan.rollback_task_ids == ("client", "schema")

    candidate = coordinator.prepare_rollback(
        plan, verification_argv=["git", "status", "--short"]
    )
    coordinator.approve_rollback(
        candidate,
        approved_plan_sha256=plan.plan_sha256,
        approved_review_sha256=candidate.review.review_sha256,
    )

    assert (repo / "schema.txt").read_text(encoding="utf-8") == "v1\n"
    assert not (repo / "client.txt").exists()
    assert ledger.get("schema").state == "rolled_back"
    assert ledger.get("client").state == "rolled_back"
    ledger.close()


def test_review_refuses_parent_head_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    workflow = GitWorktreeManager(repo, tmp_path / "worktrees")
    receipt = workflow.create()
    (receipt.path / "agent.txt").write_text("candidate\n", encoding="utf-8")
    from miniclaude.execution.worktrees import WorktreeReviewWorkflow

    review_workflow = WorktreeReviewWorkflow(workflow)
    review = review_workflow.review(
        receipt,
        verification_argv=["git", "status", "--short"],
        commit_message="agent candidate",
    )

    (repo / "other.txt").write_text("drift\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "parent drift")
    with pytest.raises(RuntimeError, match="parent HEAD changed"):
        review_workflow.approve_and_merge(
            review, approved_review_sha256=review.review_sha256
        )
    workflow.remove(receipt, discard_changes=True)


@pytest.mark.asyncio
async def test_contract_write_set_and_causal_journal_are_enforced(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "allowed.txt").write_text("old\n", encoding="utf-8")
    (project / "denied.txt").write_text("keep\n", encoding="utf-8")
    config = MiniClaudeConfig(
        project_dir=str(project),
        transactional_changes_only=True,
        allow_shell=False,
    )
    registry = ToolRegistry(config)
    registry.register_all()
    executor = ToolExecutor(registry, config)
    context = ToolExecutionContext(
        session_id="session-a",
        turn_id="turn-a",
        step_id="step-a",
        agent_id="subagent:task-a",
        capability_set=CapabilitySet.for_project(project, set(registry.get_names())),
        metadata={
            "task_id": "task-a",
            "parent_task_id": "root",
            "worktree_id": "wt-a",
            "declared_write_paths": json.dumps(["allowed.txt"]),
            "declared_read_paths": json.dumps(["allowed.txt"]),
            "dependency_task_ids": json.dumps([]),
        },
    )

    denied = await executor.execute_single(
        "apply_patch_set",
        {"patch": "--- a/denied.txt\n+++ b/denied.txt\n@@ -1 +1 @@\n-keep\n+bad\n"},
        context,
    )
    assert denied.success is False
    assert "task contract write set" in denied.content

    allowed = await executor.execute_single(
        "apply_patch_set",
        {"patch": "--- a/allowed.txt\n+++ b/allowed.txt\n@@ -1 +1 @@\n-old\n+new\n"},
        context,
    )
    assert allowed.success is True
    receipts = DurableChangeEngine(project).transactions_for_task("task-a")
    assert len(receipts) == 1
    assert receipts[0].causal["agent_id"] == "subagent:task-a"
    assert receipts[0].causal["declared_write_paths"] == ["allowed.txt"]
