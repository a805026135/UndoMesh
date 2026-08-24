from __future__ import annotations

from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from undomesh.execution.patches import PatchSet, PatchValidationError
from undomesh.execution.recovery import RestoreConflictError, RestoreState
from undomesh.execution.transactions import DurableChangeEngine, RetentionPolicy


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _change(engine: DurableChangeEngine, path: str, before: str, after: str):
    target = engine.root / path
    if not target.exists():
        target.write_text(before + "\n", encoding="utf-8")
    return engine.apply(PatchSet.parse(
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before}\n+{after}\n"
    ))


def test_multi_transaction_restore_is_independent_and_restores_oldest_state(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path)
    first = _change(engine, "chain.txt", "zero", "one")
    second = _change(engine, "chain.txt", "one", "two")

    preview = engine.preview_restore((first.transaction_id, second.transaction_id))
    receipt = engine.restore_transactions((first.transaction_id, second.transaction_id))

    assert preview.health == "available"
    assert receipt.state == RestoreState.COMPLETED
    assert (tmp_path / "chain.txt").read_text(encoding="utf-8") == "zero\n"
    assert engine.status(first.transaction_id).state.value == "committed"
    assert engine.status(second.transaction_id).state.value == "committed"
    assert engine.restore_coordinator.status(receipt.restore_id).state == RestoreState.COMPLETED


def test_restore_preflights_every_path_before_first_write(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path)
    first = _change(engine, "one.txt", "one-before", "one-after")
    second = _change(engine, "two.txt", "two-before", "two-after")
    (tmp_path / "two.txt").write_text("newer-user-content\n", encoding="utf-8")

    preview = engine.preview_restore((first.transaction_id, second.transaction_id))
    with pytest.raises(RestoreConflictError, match="newer content"):
        engine.restore_transactions((first.transaction_id, second.transaction_id))

    assert preview.health == "conflicted"
    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "one-after\n"
    assert (tmp_path / "two.txt").read_text(encoding="utf-8") == "newer-user-content\n"


def test_mid_restore_failure_compensates_to_pre_restore_workspace(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path)
    (tmp_path / "a.txt").write_text("a0\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b0\n", encoding="utf-8")
    engine = DurableChangeEngine(tmp_path)
    source = engine.apply(PatchSet.parse(
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a0\n+a1\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-b0\n+b1\n"
    ))
    coordinator = engine.restore_coordinator
    original_install = coordinator._install_target
    failed = False

    def fail_once(relative: str, snapshot, materialized):
        nonlocal failed
        if relative == "b.txt" and not failed:
            failed = True
            raise OSError("simulated restore interruption")
        return original_install(relative, snapshot, materialized)

    monkeypatch.setattr(coordinator, "_install_target", fail_once)
    with pytest.raises(PatchValidationError, match="simulated restore interruption"):
        engine.restore_transactions((source.transaction_id,))

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a1\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b1\n"
    latest = engine.restore_coordinator.list_recent(1)[0]
    assert latest.state == RestoreState.FAILED
    assert latest.recovered


def test_restore_lease_prevents_retention_gc(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(
        tmp_path, retention_policy=RetentionPolicy(0, 0, 0, 0, 1024 * 1024)
    )
    source = _change(engine, "leased.txt", "before", "after")
    coordinator = engine.restore_coordinator
    plan = coordinator._build_plan((source.transaction_id,))
    restore_id = str(uuid4())
    coordinator._write_lease(restore_id, plan)

    engine.prune(dry_run=False)

    digest = plan["targets"]["leased.txt"]["digest"]
    assert engine.backup_store.contains(digest)
    coordinator._release_lease(restore_id)


def test_startup_recovers_interrupted_restore_to_expected_state(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path)
    source = _change(engine, "crash.txt", "before", "after")
    coordinator = engine.restore_coordinator
    plan = coordinator._build_plan((source.transaction_id,))
    restore_id = str(uuid4())
    journal = {
        "schema_version": 1,
        "restore_id": restore_id,
        "state": RestoreState.COMMITTING.value,
        "source_transaction_ids": plan["transaction_ids"],
        "touched_paths": list(plan["targets"]),
        "targets": plan["targets"],
        "expected": plan["expected"],
        "committed_paths": ["crash.txt"],
    }
    coordinator._write_journal(journal)
    coordinator._write_lease(restore_id, plan)
    engine._restore(tmp_path / "crash.txt", engine._snapshot(plan["targets"]["crash.txt"]))

    recovered = engine.recover_incomplete_restores()

    assert recovered[0].state == RestoreState.ABORTED
    assert recovered[0].recovered
    assert (tmp_path / "crash.txt").read_text(encoding="utf-8") == "after\n"
    assert not (coordinator.leases / f"{restore_id}.json").exists()


def test_conflicted_crash_restore_keeps_lease_until_explicit_abort(tmp_path: Path) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path)
    source = _change(engine, "conflict.txt", "before", "after")
    coordinator = engine.restore_coordinator
    plan = coordinator._build_plan((source.transaction_id,))
    restore_id = str(uuid4())
    coordinator._write_journal({
        "schema_version": 1,
        "restore_id": restore_id,
        "state": RestoreState.COMMITTING.value,
        "source_transaction_ids": plan["transaction_ids"],
        "touched_paths": list(plan["targets"]),
        "targets": plan["targets"],
        "expected": plan["expected"],
        "committed_paths": [],
    })
    coordinator._write_lease(restore_id, plan)
    (tmp_path / "conflict.txt").write_text("unknown\n", encoding="utf-8")

    recovered = coordinator.recover_incomplete()

    assert recovered[0].state == RestoreState.CONFLICTED
    assert (coordinator.leases / f"{restore_id}.json").exists()
    aborted = coordinator.abort(restore_id)
    assert aborted.state == RestoreState.ABORTED
    assert not (coordinator.leases / f"{restore_id}.json").exists()
    assert (tmp_path / "conflict.txt").read_text(encoding="utf-8") == "unknown\n"


def test_startup_survives_failed_crash_compensation_and_retains_lease(
    tmp_path: Path, monkeypatch
) -> None:
    _repo(tmp_path)
    engine = DurableChangeEngine(tmp_path)
    source = _change(engine, "damaged.txt", "before", "after")
    coordinator = engine.restore_coordinator
    plan = coordinator._build_plan((source.transaction_id,))
    restore_id = str(uuid4())
    coordinator._write_journal({
        "schema_version": 1,
        "restore_id": restore_id,
        "state": RestoreState.COMMITTING.value,
        "source_transaction_ids": plan["transaction_ids"],
        "touched_paths": list(plan["targets"]),
        "targets": plan["targets"],
        "expected": plan["expected"],
        "committed_paths": [],
    })
    coordinator._write_lease(restore_id, plan)

    def fail_compensation(_snapshots):
        raise OSError("damaged recovery payload")

    monkeypatch.setattr(engine, "_restore_map", fail_compensation)
    recovered = coordinator.recover_incomplete()

    assert recovered[0].state == RestoreState.CONFLICTED
    assert "compensation failed" in recovered[0].error.lower()
    assert (coordinator.leases / f"{restore_id}.json").exists()
