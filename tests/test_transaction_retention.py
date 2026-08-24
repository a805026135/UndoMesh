from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

from miniclaude.core.tool_executor import ToolExecutor
from miniclaude.security.permission import PermissionManager
from miniclaude.tools.registry import ToolRegistry
from miniclaude.execution.patches import PatchSet
from miniclaude.execution.transactions import (
    DurableChangeEngine,
    RetentionPolicy,
    TransactionConflictError,
)


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _change(engine: DurableChangeEngine, path: Path, before: str, after: str):
    path.write_text(before + "\n", encoding="utf-8")
    patch = PatchSet.parse(
        f"--- a/{path.name}\n+++ b/{path.name}\n@@ -1 +1 @@\n-{before}\n+{after}\n"
    )
    return engine.apply(patch)


def _immediate_policy(max_bytes: int = 1024 * 1024) -> RetentionPolicy:
    return RetentionPolicy(
        committed_days=0,
        terminal_days=0,
        trash_grace_days=0,
        orphan_grace_hours=0,
        max_backup_bytes=max_bytes,
    )


def test_dry_run_reports_without_mutating_journal_or_blobs(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy())
    receipt = _change(engine, tmp_path / "dry.txt", "before", "after")
    journal_before = engine._journal_path(receipt.transaction_id).read_bytes()
    blobs_before = {path.name for path in engine.blobs.iterdir()}

    report = engine.prune(dry_run=True)

    assert receipt.transaction_id in report.eligible_transactions
    assert report.blobs_to_trash
    assert engine._journal_path(receipt.transaction_id).read_bytes() == journal_before
    assert {path.name for path in engine.blobs.iterdir()} == blobs_before


def test_finalize_then_two_phase_prune_expires_rollback_and_releases_bytes(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy())
    receipt = _change(engine, tmp_path / "accepted.txt", "before", "after")
    finalized = engine.finalize(receipt.transaction_id)
    assert not finalized.rollback_available
    assert finalized.recoverability == "pending_prune"

    first = engine.prune(dry_run=False)
    expired = engine.status(receipt.transaction_id)
    assert receipt.transaction_id in first.eligible_transactions
    assert expired.recoverability == "expired"
    assert expired.backup_expired_at
    assert not list(engine.blobs.iterdir())
    assert not list(engine.manifests.iterdir())
    assert not list(engine.chunks.iterdir())
    assert list(engine.trash.iterdir())
    with pytest.raises(TransactionConflictError, match="no longer available"):
        engine.rollback(receipt.transaction_id)

    later = datetime.now(timezone.utc) + timedelta(seconds=2)
    second = engine.prune(dry_run=False, now=later)
    assert second.released_bytes > 0
    assert not list(engine.trash.iterdir())


def test_shared_blob_remains_while_another_transaction_references_it(tmp_path: Path) -> None:
    _repo(tmp_path)
    policy = RetentionPolicy(30, 7, 0, 0, 1024 * 1024)
    engine = DurableChangeEngine(tmp_path, retention_policy=policy)
    first = _change(engine, tmp_path / "one.txt", "shared", "first")
    second = _change(engine, tmp_path / "two.txt", "shared", "second")
    first_journal = json.loads(engine._journal_path(first.transaction_id).read_text(encoding="utf-8"))
    shared_digest = first_journal["preimages"]["one.txt"]["digest"]
    engine.finalize(first.transaction_id)

    engine.prune(dry_run=False)

    assert engine.status(second.transaction_id).rollback_available
    assert (engine.manifests / f"{shared_digest}.json").exists()
    assert engine.backup_store.load(shared_digest).decode("utf-8").strip() == "shared"


def test_pinned_transaction_never_expires_automatically(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy())
    receipt = _change(engine, tmp_path / "pinned.txt", "before", "after")
    engine.set_pinned(receipt.transaction_id, True)

    report = engine.prune(dry_run=False)

    assert receipt.transaction_id in report.protected_transactions
    assert engine.status(receipt.transaction_id).rollback_available


def test_active_transaction_and_its_blob_are_never_pruned(tmp_path: Path) -> None:
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy())
    target = tmp_path / "active.txt"
    target.write_text("active", encoding="utf-8")
    snapshot = engine._capture(target)
    transaction_id = "f863a76f-817a-4b66-9ca2-63e79cd614d9"
    engine._write_journal({
        "schema_version": 1,
        "transaction_id": transaction_id,
        "patch_id": "active",
        "state": "committing",
        "touched_paths": ["active.txt"],
        "preimages": {"active.txt": {"kind": "file", "digest": snapshot.digest, "mode": snapshot.mode, "link_target": None}},
        "postimages": {"active.txt": {"kind": "absent", "digest": None, "mode": None, "link_target": None}},
        "verification": [],
        "conflict_paths": [],
    })

    report = engine.prune(dry_run=False)

    assert transaction_id in report.protected_transactions
    assert (engine.manifests / f"{snapshot.digest}.json").exists()
    assert engine.backup_store.load(str(snapshot.digest)) == b"active"


def test_backup_budget_fails_closed_without_changing_workspace(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy(max_bytes=1))
    target = tmp_path / "large.txt"
    target.write_text("original-content\n", encoding="utf-8")
    patch = PatchSet.parse(
        "--- a/large.txt\n+++ b/large.txt\n@@ -1 +1 @@\n-original-content\n+changed-content\n"
    )

    with pytest.raises(ValueError, match="budget exceeded"):
        engine.apply(patch)

    assert target.read_text(encoding="utf-8") == "original-content\n"
    assert engine.list_recent(1)[0].state.value == "rejected"


def test_unpin_allows_later_policy_expiry(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy())
    receipt = _change(engine, tmp_path / "toggle.txt", "before", "after")
    engine.set_pinned(receipt.transaction_id, True)
    engine.set_pinned(receipt.transaction_id, False)

    report = engine.prune(dry_run=False)

    assert receipt.transaction_id in report.eligible_transactions


def test_prune_keeps_chunks_shared_by_a_different_protected_manifest(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy(max_bytes=4 * 1024 * 1024))
    common = "common-block-" * 20_000
    first_path = tmp_path / "first-large.txt"
    second_path = tmp_path / "second-large.txt"
    first = _change(engine, first_path, common + "A", common + "changed-A")
    # Keep the second transaction protected while the first is explicitly finalized.
    engine.retention_policy = RetentionPolicy(30, 7, 0, 0, 4 * 1024 * 1024)
    second = _change(engine, second_path, common + "B", common + "changed-B")
    second_journal = json.loads(engine._journal_path(second.transaction_id).read_text(encoding="utf-8"))
    second_preimage = second_journal["preimages"]["second-large.txt"]["digest"]
    engine.finalize(first.transaction_id)

    engine.prune(dry_run=False)

    assert engine.backup_store.load(second_preimage).decode("utf-8").rstrip().endswith("B")


def test_expiry_order_prefers_lower_importance_then_older_transaction(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path, retention_policy=_immediate_policy())
    critical = _change(engine, tmp_path / "pyproject.toml", "before", "after")
    documentation = _change(engine, tmp_path / "notes.md", "before", "after")

    report = engine.prune(dry_run=True)

    assert report.eligible_transactions.index(documentation.transaction_id) < report.eligible_transactions.index(
        critical.transaction_id
    )


@pytest.mark.asyncio
async def test_real_prune_requires_confirmation_but_preview_does_not(config, tmp_project: Path) -> None:
    configured = config.model_copy(update={"project_dir": str(tmp_project)})
    registry = ToolRegistry(configured)
    registry.register_all()
    executor = ToolExecutor(
        registry,
        configured,
        permission_manager=PermissionManager(configured),
    )

    preview = await executor.execute_single("prune_change_transactions", {"dry_run": True})
    actual = await executor.execute_single("prune_change_transactions", {"dry_run": False})

    assert preview.success
    assert not actual.success
    assert "Confirmation required" in actual.content


@pytest.mark.asyncio
async def test_finalize_succeeds_only_after_explicit_approval(config, tmp_project: Path) -> None:
    _repo(tmp_project)
    configured = config.model_copy(update={"project_dir": str(tmp_project)})
    receipt = _change(DurableChangeEngine(tmp_project), tmp_project / "approved.txt", "before", "after")
    registry = ToolRegistry(configured)
    registry.register_all()
    permissions = PermissionManager(configured)
    executor = ToolExecutor(registry, configured, permission_manager=permissions)

    denied = await executor.execute_single(
        "finalize_change_transaction", {"transaction_id": receipt.transaction_id}
    )
    permissions.set_confirm_callback(lambda _name, _params: True)
    approved = await executor.execute_single(
        "finalize_change_transaction", {"transaction_id": receipt.transaction_id}
    )

    assert not denied.success
    assert approved.success
    assert approved.metadata["recoverability"] == "pending_prune"


@pytest.mark.asyncio
async def test_storage_status_tool_reports_two_gibibyte_capacity(config, tmp_project: Path) -> None:
    configured = config.model_copy(update={"project_dir": str(tmp_project)})
    registry = ToolRegistry(configured)
    registry.register_all()
    result = await ToolExecutor(registry, configured).execute_single("transaction_storage_status", {})

    assert result.success
    assert result.metadata["capacity_bytes"] == 2 * 1024 * 1024 * 1024
    assert result.metadata["encryption_enabled"] is True
