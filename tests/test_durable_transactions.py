from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from miniclaude.execution.patches import PatchSet
from miniclaude.execution.transactions import (
    DurableChangeEngine,
    TransactionConflictError,
    TransactionState,
)
from miniclaude.execution.recovery import RestoreState


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_deleted_file_is_restored_by_explicit_rollback(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "delete_me.txt"
    target.write_text("important\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/delete_me.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-important\n")
    engine = DurableChangeEngine(tmp_path)

    committed = engine.apply(patch)
    assert committed.state == TransactionState.COMMITTED
    assert not target.exists()

    rolled_back = engine.rollback(committed.transaction_id)
    assert rolled_back.state == RestoreState.COMPLETED
    assert engine.status(committed.transaction_id).state == TransactionState.COMMITTED
    assert target.read_text(encoding="utf-8") == "important\n"


def test_new_file_is_removed_by_rollback(tmp_path: Path) -> None:
    _repo(tmp_path)
    patch = PatchSet.parse("--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+new\n")
    engine = DurableChangeEngine(tmp_path)
    committed = engine.apply(patch)
    assert (tmp_path / "new.txt").exists()
    engine.rollback(committed.transaction_id)
    assert not (tmp_path / "new.txt").exists()


def test_renamed_file_is_moved_back_by_rollback(tmp_path: Path) -> None:
    _repo(tmp_path)
    old = tmp_path / "old.txt"
    old.write_text("identity\n", encoding="utf-8")
    patch = PatchSet.parse(
        "diff --git a/old.txt b/new.txt\n"
        "similarity index 100%\n"
        "rename from old.txt\n"
        "rename to new.txt\n"
    )
    engine = DurableChangeEngine(tmp_path)

    receipt = engine.apply(patch)
    assert receipt.state == TransactionState.COMMITTED
    assert not old.exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "identity\n"

    engine.rollback(receipt.transaction_id)
    assert old.read_text(encoding="utf-8") == "identity\n"
    assert not (tmp_path / "new.txt").exists()


def test_verification_side_effects_stay_in_stage_and_failure_leaves_root_untouched(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "code.py"
    target.write_text("old = True\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-old = True\n+old = False\n")
    engine = DurableChangeEngine(tmp_path)

    receipt = engine.apply(
        patch,
        verify=(f'"{sys.executable}" -m pytest missing_test.py -q',),
    )

    assert receipt.state == TransactionState.REJECTED
    assert target.read_text(encoding="utf-8") == "old = True\n"
    assert not (tmp_path / ".pytest_cache").exists()


def test_optimistic_conflict_does_not_overwrite_user_edit(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path)
    target = tmp_path / "shared.txt"
    target.write_text("base\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/shared.txt\n+++ b/shared.txt\n@@ -1 +1 @@\n-base\n+agent\n")
    engine = DurableChangeEngine(tmp_path)
    original = engine._create_stage

    def create_then_user_edit(transaction_id: str):
        stage = original(transaction_id)
        target.write_text("user\n", encoding="utf-8")
        return stage

    monkeypatch.setattr(engine, "_create_stage", create_then_user_edit)
    receipt = engine.apply(patch)
    assert receipt.state == TransactionState.CONFLICTED
    assert receipt.conflict_paths == ("shared.txt",)
    assert target.read_text(encoding="utf-8") == "user\n"


def test_rollback_refuses_to_overwrite_newer_edit(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "shared.txt"
    target.write_text("base\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/shared.txt\n+++ b/shared.txt\n@@ -1 +1 @@\n-base\n+agent\n")
    engine = DurableChangeEngine(tmp_path)
    receipt = engine.apply(patch)
    target.write_text("newer-user-edit\n", encoding="utf-8")
    with pytest.raises(TransactionConflictError, match="newer"):
        engine.rollback(receipt.transaction_id)
    assert target.read_text(encoding="utf-8") == "newer-user-edit\n"


def test_startup_recovery_restores_preimages_after_interrupted_commit(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "recover.txt"
    target.write_text("before\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/recover.txt\n+++ b/recover.txt\n@@ -1 +1 @@\n-before\n+after\n")
    engine = DurableChangeEngine(tmp_path)
    receipt = engine.apply(patch)
    journal_path = engine.journals / f"{receipt.transaction_id}.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["state"] = TransactionState.COMMITTING.value
    engine._write_journal(journal)
    target.write_text("after\n", encoding="utf-8")

    recovered = DurableChangeEngine(tmp_path).recover_incomplete()

    assert recovered[0].recovered
    assert recovered[0].state == TransactionState.ROLLED_BACK
    assert target.read_text(encoding="utf-8") == "before\n"


def test_startup_recovery_preserves_unknown_newer_content(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "recover.txt"
    target.write_text("before\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/recover.txt\n+++ b/recover.txt\n@@ -1 +1 @@\n-before\n+after\n")
    engine = DurableChangeEngine(tmp_path)
    receipt = engine.apply(patch)
    journal = json.loads((engine.journals / f"{receipt.transaction_id}.json").read_text(encoding="utf-8"))
    journal["state"] = TransactionState.COMMITTING.value
    engine._write_journal(journal)
    target.write_text("newer-user-content\n", encoding="utf-8")

    recovered = engine.recover_incomplete()

    assert recovered[0].state == TransactionState.RECOVERY_CONFLICTED
    assert recovered[0].conflict_paths == ("recover.txt",)
    assert target.read_text(encoding="utf-8") == "newer-user-content\n"


def test_verifier_does_not_inherit_provider_credentials(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "must-not-leak")
    monkeypatch.setenv("MINICLAUDE_TRANSACTION_BACKUP_KEY", "also-must-not-leak")
    (tmp_path / "verify_env_test.py").write_text(
        "import os\ndef test_no_key():\n"
        "    assert 'DASHSCOPE_API_KEY' not in os.environ\n"
        "    assert 'MINICLAUDE_TRANSACTION_BACKUP_KEY' not in os.environ\n",
        encoding="utf-8",
    )
    target = tmp_path / "safe.txt"
    target.write_text("before\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/safe.txt\n+++ b/safe.txt\n@@ -1 +1 @@\n-before\n+after\n")

    receipt = DurableChangeEngine(tmp_path).apply(
        patch, verify=(f'"{sys.executable}" -m pytest verify_env_test.py -q',)
    )

    assert receipt.state == TransactionState.COMMITTED


def test_mid_commit_failure_restores_every_preimage(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    patch = PatchSet.parse(
        "--- a/first.txt\n+++ b/first.txt\n@@ -1 +1 @@\n-one\n+changed-one\n"
        "--- a/second.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-two\n"
    )
    engine = DurableChangeEngine(tmp_path)
    restore = engine._restore
    failed = False

    def fail_once(path: Path, snapshot):
        nonlocal failed
        if path.name == "second.txt" and not failed:
            failed = True
            raise OSError("simulated disk interruption")
        return restore(path, snapshot)

    monkeypatch.setattr(engine, "_restore", fail_once)
    with pytest.raises(ValueError, match="simulated disk interruption"):
        engine.apply(patch)

    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"
    assert engine.list_recent(1)[0].state == TransactionState.ROLLED_BACK


def test_interrupted_precommit_transaction_is_abandoned_without_touching_root(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "safe.txt"
    target.write_text("safe\n", encoding="utf-8")
    engine = DurableChangeEngine(tmp_path)
    transaction_id = "d390e4e3-e3b8-4f46-b5fc-cf6138a8e66a"
    engine._write_journal({
        "schema_version": 1,
        "transaction_id": transaction_id,
        "patch_id": "test",
        "state": TransactionState.STAGED.value,
        "touched_paths": ["safe.txt"],
        "verification": [],
        "conflict_paths": [],
    })

    recovered = engine.recover_incomplete()

    assert recovered[0].state == TransactionState.REJECTED
    assert recovered[0].recovered
    assert target.read_text(encoding="utf-8") == "safe\n"


def test_transaction_lookup_rejects_path_traversal(tmp_path: Path) -> None:
    engine = DurableChangeEngine(tmp_path)
    with pytest.raises(ValueError, match="Invalid transaction ID"):
        engine.status("../../outside")


def test_transaction_receipt_records_state_history(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "history.txt").write_text("before\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/history.txt\n+++ b/history.txt\n@@ -1 +1 @@\n-before\n+after\n")
    engine = DurableChangeEngine(tmp_path)

    receipt = engine.apply(patch)

    assert receipt.created_at and receipt.updated_at
    assert receipt.transitions[-1][1] == TransactionState.COMMITTED.value
    assert [target for _, target, _ in receipt.transitions] == [
        "proposed", "prepared", "staged", "verified", "committing", "committed"
    ]


def test_restore_refuses_parent_symlink_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    try:
        (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    engine = DurableChangeEngine(tmp_path)

    with pytest.raises(ValueError, match="workspace symlink"):
        engine._workspace_path("linked/escape.txt")
    assert not (outside / "escape.txt").exists()
