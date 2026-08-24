from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from undomesh.core.tool_executor import ToolExecutor
from undomesh.execution.backup_store import EncryptedChunkStore
from undomesh.execution.transactions import (
    DurableChangeEngine,
    HotVaultPolicy,
    TransactionConflictError,
)
from undomesh.execution.patches import PatchValidationError
from undomesh.security.permission import PermissionManager
from undomesh.tools.registry import ToolRegistry


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _hot_policy(*, ttl_hours: float = 24) -> HotVaultPolicy:
    return HotVaultPolicy(
        threshold_bytes=1,
        ttl_hours=ttl_hours,
        max_backup_bytes=8 * 1024 * 1024,
        minimum_free_bytes=0,
    )


def test_stream_store_and_restore_do_not_require_whole_file_reads(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.bin"
    restored = tmp_path / "restored.bin"
    content = (bytes(range(256)) * 4096) + b"tail"
    source.write_bytes(content)
    store = EncryptedChunkStore(tmp_path / "store", encryption=False)

    original_read_bytes = Path.read_bytes

    def reject_read_bytes(candidate: Path) -> bytes:
        if candidate == source:
            raise AssertionError("whole-file source read_bytes was used")
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    digest = store.store_path(source, max_bytes=4 * 1024 * 1024)
    with restored.open("wb") as handle:
        written = store.write_to(digest, handle)

    assert written == len(content)
    assert restored.open("rb").read() == content


def test_large_file_delete_uses_hot_vault_and_rolls_back_exactly(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "large.bin"
    content = bytes(range(256)) * 8192
    target.write_bytes(content)
    engine = DurableChangeEngine(
        tmp_path,
        backup_encryption=False,
        hot_vault_policy=_hot_policy(),
    )

    receipt = engine.delete_files(("large.bin",))
    journal = engine._read_journal(receipt.transaction_id)

    assert not target.exists()
    assert journal["preimages"]["large.bin"]["storage_tier"] == "hot"
    assert engine.storage_status()["hot_vault"]["manifest_count"] == 1

    rolled_back = engine.rollback(receipt.transaction_id)

    assert rolled_back.state.value == "completed"
    assert target.read_bytes() == content


def test_hot_backup_expires_after_ttl_and_reports_unrecoverable(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "temporary.bin"
    target.write_bytes(b"important" * 2048)
    engine = DurableChangeEngine(
        tmp_path,
        backup_encryption=False,
        hot_vault_policy=_hot_policy(ttl_hours=1),
    )
    receipt = engine.delete_files(("temporary.bin",))
    journal = engine._read_journal(receipt.transaction_id)
    expiry = datetime.fromisoformat(journal["preimages"]["temporary.bin"]["backup_expires_at"])

    report = engine.prune(dry_run=False, now=expiry + timedelta(seconds=1))
    expired = engine.status(receipt.transaction_id)

    assert report.hot_manifests_deleted
    assert report.hot_released_bytes > 0
    assert expired.recoverability == "expired"
    assert not expired.rollback_available
    assert expired.hot_backup_expired_at
    with pytest.raises(TransactionConflictError, match="no longer available"):
        engine.rollback(receipt.transaction_id)


def test_hot_vault_admission_failure_leaves_original_file_untouched(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "too-large.bin"
    content = b"cannot-fit" * 4096
    target.write_bytes(content)
    engine = DurableChangeEngine(
        tmp_path,
        backup_encryption=False,
        hot_vault_policy=HotVaultPolicy(
            threshold_bytes=1,
            ttl_hours=24,
            max_backup_bytes=32,
            minimum_free_bytes=0,
        ),
    )

    with pytest.raises(PatchValidationError, match="budget.*exceeded"):
        engine.delete_files(("too-large.bin",))

    assert target.read_bytes() == content
    assert engine.list_recent(1)[0].state.value == "rejected"


def test_pin_extends_hot_backup_beyond_default_ttl(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "pinned.bin"
    target.write_bytes(b"keep" * 4096)
    engine = DurableChangeEngine(
        tmp_path,
        backup_encryption=False,
        hot_vault_policy=_hot_policy(ttl_hours=1),
    )
    receipt = engine.delete_files(("pinned.bin",))
    engine.set_pinned(receipt.transaction_id, True)

    engine.prune(
        dry_run=False,
        now=datetime.now(timezone.utc) + timedelta(days=2),
    )

    assert engine.status(receipt.transaction_id).rollback_available
    engine.rollback(receipt.transaction_id)
    assert target.read_bytes() == b"keep" * 4096


def test_recovery_audit_reports_available_conflicted_and_corrupt(tmp_path: Path) -> None:
    _repo(tmp_path)
    target = tmp_path / "audit.bin"
    target.write_bytes(b"audit" * 8192)
    engine = DurableChangeEngine(
        tmp_path,
        backup_encryption=False,
        hot_vault_policy=_hot_policy(),
    )
    receipt = engine.delete_files(("audit.bin",))

    assert engine.audit_recovery(receipt.transaction_id).health == "available"

    target.write_bytes(b"new-user-content")
    conflict = engine.audit_recovery(receipt.transaction_id)
    assert conflict.health == "conflicted"
    assert conflict.conflict_paths == ("audit.bin",)

    target.unlink()
    chunk = next(engine.hot_store.chunks.glob("*.chunk"))
    encoded = bytearray(chunk.read_bytes())
    encoded[-1] ^= 1
    chunk.write_bytes(encoded)
    corrupt = engine.audit_recovery(receipt.transaction_id)
    assert corrupt.health == "corrupt"
    assert corrupt.unavailable_paths == ("audit.bin",)


@pytest.mark.asyncio
async def test_permanent_hot_purge_requires_token_and_fresh_approval(config, tmp_project: Path) -> None:
    _repo(tmp_project)
    configured = config.model_copy(update={
        "project_dir": str(tmp_project),
        "transaction_hot_threshold_bytes": 1,
        "transaction_hot_minimum_free_bytes": 0,
        "transaction_hot_max_backup_bytes": 8 * 1024 * 1024,
    })
    target = tmp_project / "purge.bin"
    target.write_bytes(b"purge-me" * 2048)
    receipt = DurableChangeEngine(
        tmp_project,
        hot_vault_policy=configured.transaction_hot_vault_policy(),
    ).delete_files(("purge.bin",))
    registry = ToolRegistry(configured)
    registry.register_all()
    permissions = PermissionManager(configured)
    executor = ToolExecutor(registry, configured, permission_manager=permissions)

    preview = await executor.execute_single(
        "purge_large_backup_cache",
        {"transaction_ids": [receipt.transaction_id], "dry_run": True},
    )
    denied = await executor.execute_single(
        "purge_large_backup_cache",
        {
            "transaction_ids": [receipt.transaction_id],
            "dry_run": False,
            "purge_token": preview.metadata["purge_token"],
        },
    )
    approvals = 0

    def approve(_name: str, _params: dict) -> bool:
        nonlocal approvals
        approvals += 1
        return True

    permissions.set_confirm_callback(approve)
    wrong_scope = await executor.execute_single(
        "purge_large_backup_cache",
        {
            "transaction_ids": [receipt.transaction_id],
            "dry_run": False,
            "purge_token": "0" * 64,
        },
    )
    purged = await executor.execute_single(
        "purge_large_backup_cache",
        {
            "transaction_ids": [receipt.transaction_id],
            "dry_run": False,
            "purge_token": preview.metadata["purge_token"],
        },
    )

    assert preview.success
    assert not denied.success and "Confirmation required" in denied.content
    assert not wrong_scope.success and "scope changed" in wrong_scope.content
    assert purged.success and purged.metadata["permanent"] is True
    assert approvals == 2
    assert not DurableChangeEngine(
        tmp_project,
        hot_vault_policy=configured.transaction_hot_vault_policy(),
    ).status(receipt.transaction_id).rollback_available
