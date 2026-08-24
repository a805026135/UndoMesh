"""Durable, isolated and crash-recoverable workspace change transactions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from contextlib import contextmanager
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any
from uuid import UUID, uuid4

from miniclaude.execution.patches import GitPatchWorkflow, PatchSet, PatchValidationError
from miniclaude.execution.sandbox import sanitized_subprocess_env
from miniclaude.execution.backup_store import (
    BackupBudgetError, BackupStoreError, ChunkingPolicy, EncryptedChunkStore,
)


class TransactionState(StrEnum):
    PROPOSED = "proposed"
    PREPARED = "prepared"
    STAGED = "staged"
    VERIFIED = "verified"
    COMMITTING = "committing"
    COMMITTED = "committed"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"
    RECOVERING = "recovering"
    RECOVERY_CONFLICTED = "recovery_conflicted"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class PathSnapshot:
    kind: str
    digest: str | None = None
    mode: int | None = None
    link_target: str | None = None
    storage_tier: str | None = None
    backup_expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class TransactionReceipt:
    transaction_id: str
    patch_id: str
    state: TransactionState
    touched_paths: tuple[str, ...]
    verification: tuple[tuple[str, int], ...] = ()
    conflict_paths: tuple[str, ...] = ()
    recovered: bool = False
    created_at: str = ""
    updated_at: str = ""
    transitions: tuple[tuple[str, str, str], ...] = ()
    pinned: bool = False
    rollback_available: bool = False
    recoverability: str = "available"
    finalized_at: str = ""
    backup_expired_at: str = ""
    hot_backup_expired_at: str = ""
    importance: int = 50
    causal: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    committed_days: int = 30
    terminal_days: int = 7
    trash_grace_days: int = 1
    orphan_grace_hours: int = 1
    max_backup_bytes: int = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HotVaultPolicy:
    """Short-lived overflow protection for files too large for the warm pool."""

    threshold_bytes: int = 256 * 1024 * 1024
    ttl_hours: float = 24.0
    max_backup_bytes: int = 50 * 1024 * 1024 * 1024
    minimum_free_bytes: int = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PruneReport:
    dry_run: bool
    eligible_transactions: tuple[str, ...]
    protected_transactions: tuple[str, ...]
    blobs_to_trash: tuple[str, ...]
    trash_to_delete: tuple[str, ...]
    reclaimable_bytes: int
    released_bytes: int
    storage_bytes: int
    over_budget_bytes: int
    hot_manifests_deleted: tuple[str, ...] = ()
    hot_released_bytes: int = 0
    hot_storage_bytes: int = 0


@dataclass(frozen=True, slots=True)
class HotPurgePreview:
    transaction_ids: tuple[str, ...]
    file_digests: tuple[str, ...]
    logical_bytes: int
    purge_token: str


@dataclass(frozen=True, slots=True)
class RecoveryHealthReport:
    transaction_id: str
    health: str
    checked_paths: tuple[str, ...]
    unavailable_paths: tuple[str, ...]
    conflict_paths: tuple[str, ...]
    detail: str
    checked_at: str


class TransactionConflictError(RuntimeError):
    pass


class DurableChangeEngine:
    """Stage, verify and durably commit one declared patch."""

    IGNORE = (".git", ".miniclaude", "__pycache__", "*.pyc", ".pytest_cache", "node_modules", ".venv", "venv", "data")

    def __init__(
        self,
        root: Path,
        transaction_root: Path | None = None,
        retention_policy: RetentionPolicy | None = None,
        backup_encryption: bool = True,
        backup_encryption_key: bytes | None = None,
        chunking_policy: ChunkingPolicy | None = None,
        hot_vault_policy: HotVaultPolicy | None = None,
    ) -> None:
        self.root = root.resolve()
        self.transaction_root = (transaction_root or self.root / ".miniclaude" / "transactions").resolve()
        self.journals = self.transaction_root / "journals"
        self.backup_store = EncryptedChunkStore(
            self.transaction_root,
            encryption=backup_encryption,
            encryption_key=backup_encryption_key,
            chunking=chunking_policy,
        )
        self.hot_vault_policy = hot_vault_policy or HotVaultPolicy()
        self.hot_root = self.transaction_root / "hot-vault"
        self.hot_store = EncryptedChunkStore(
            self.hot_root,
            encryption=backup_encryption,
            encryption_key=backup_encryption_key,
            chunking=chunking_policy,
        )
        self.blobs = self.backup_store.legacy_blobs
        self.chunks = self.backup_store.chunks
        self.manifests = self.backup_store.manifests
        self.keys = self.backup_store.keys
        self.trash = self.backup_store.trash
        self.retention_policy = retention_policy or RetentionPolicy()
        self.journals.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.transaction_root, self.journals, self.blobs, self.chunks,
            self.manifests, self.keys, self.trash,
            self.hot_root,
        ):
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        from miniclaude.execution.recovery import RestoreCoordinator
        self.restore_coordinator = RestoreCoordinator(self)

    def apply(
        self,
        patch: PatchSet,
        *,
        verify: tuple[str, ...] = (),
        causal: dict[str, Any] | None = None,
    ) -> TransactionReceipt:
        transaction_id = str(uuid4())
        journal: dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "patch_id": patch.patch_id,
            "state": TransactionState.PROPOSED.value,
            "touched_paths": list(patch.touched_paths),
            "verification": [],
            "conflict_paths": [],
            "importance": self._infer_importance(patch.touched_paths),
            "causal": self._normalize_causal(causal),
        }
        self._write_journal(journal)
        stage: Path | None = None
        root_mutation_started = False
        try:
            preimages = {path: asdict(self._capture(self._workspace_path(path))) for path in patch.touched_paths}
            journal.update(state=TransactionState.PREPARED.value, preimages=preimages)
            self._write_journal(journal)

            stage = self._create_stage(transaction_id)
            journal.update(state=TransactionState.STAGED.value, staging_path=str(stage))
            self._write_journal(journal)
            evidence = GitPatchWorkflow(stage).apply(patch, verify=verify)
            journal["verification"] = [list(item) for item in evidence.verification]
            if evidence.rolled_back:
                journal["state"] = TransactionState.REJECTED.value
                self._write_journal(journal)
                return self._receipt(journal)

            postimages = {path: asdict(self._capture(stage / path)) for path in patch.touched_paths}
            journal.update(state=TransactionState.VERIFIED.value, postimages=postimages)
            self._write_journal(journal)
            with self._commit_lock():
                conflicts = [
                    path for path in patch.touched_paths
                    if not self._same_content(
                        self._capture(self._workspace_path(path), persist=False),
                        self._snapshot(preimages[path]),
                    )
                ]
                if conflicts:
                    journal.update(state=TransactionState.CONFLICTED.value, conflict_paths=conflicts)
                    self._write_journal(journal)
                    return self._receipt(journal)

                journal["state"] = TransactionState.COMMITTING.value
                self._write_journal(journal)
                root_mutation_started = True
                for path in patch.touched_paths:
                    self._restore(self._workspace_path(path), self._snapshot(postimages[path]))
                journal["state"] = TransactionState.COMMITTED.value
                self._write_journal(journal)
            return self._receipt(journal)
        except Exception as exc:
            journal["error_type"] = type(exc).__name__
            if root_mutation_started:
                journal["state"] = TransactionState.RECOVERING.value
                self._write_journal(journal)
                with self._commit_lock():
                    self._restore_map(journal.get("preimages", {}))
                journal["state"] = TransactionState.ROLLED_BACK.value
            else:
                journal["state"] = TransactionState.REJECTED.value
            self._write_journal(journal)
            if isinstance(exc, (PatchValidationError, TransactionConflictError)):
                raise
            raise PatchValidationError(f"Change transaction failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if stage and stage.exists():
                shutil.rmtree(stage.parent, ignore_errors=True)

    def delete_files(
        self,
        paths: tuple[str, ...],
        *,
        causal: dict[str, Any] | None = None,
    ) -> TransactionReceipt:
        """Durably back up declared files before deleting them from the workspace."""
        touched = tuple(dict.fromkeys(paths))
        if not touched:
            raise PatchValidationError("At least one file path is required")
        for relative in touched:
            target = self._workspace_path(relative)
            if target.is_symlink() or not target.is_file():
                raise PatchValidationError(f"Transactional deletion requires an existing file: {relative}")
        transaction_id = str(uuid4())
        patch_id = hashlib.sha256(
            json.dumps({"operation": "delete", "paths": touched}, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        journal: dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "patch_id": patch_id,
            "operation": "delete_files",
            "state": TransactionState.PROPOSED.value,
            "touched_paths": list(touched),
            "verification": [],
            "conflict_paths": [],
            "importance": self._infer_importance(touched),
            "causal": self._normalize_causal(causal),
        }
        self._write_journal(journal)
        root_mutation_started = False
        try:
            preimages = {
                relative: asdict(self._capture(self._workspace_path(relative)))
                for relative in touched
            }
            postimages = {relative: asdict(PathSnapshot("absent")) for relative in touched}
            journal.update(
                state=TransactionState.PREPARED.value,
                preimages=preimages,
                postimages=postimages,
            )
            self._write_journal(journal)
            with self._commit_lock():
                conflicts = [
                    relative for relative in touched
                    if not self._same_content(
                        self._capture(self._workspace_path(relative), persist=False),
                        self._snapshot(preimages[relative]),
                    )
                ]
                if conflicts:
                    journal.update(
                        state=TransactionState.CONFLICTED.value,
                        conflict_paths=conflicts,
                    )
                    self._write_journal(journal)
                    return self._receipt(journal)
                journal["state"] = TransactionState.COMMITTING.value
                self._write_journal(journal)
                root_mutation_started = True
                for relative in touched:
                    self._restore(self._workspace_path(relative), PathSnapshot("absent"))
                journal["state"] = TransactionState.COMMITTED.value
                self._write_journal(journal)
            return self._receipt(journal)
        except Exception as exc:
            journal["error_type"] = type(exc).__name__
            if root_mutation_started:
                journal["state"] = TransactionState.RECOVERING.value
                self._write_journal(journal)
                with self._commit_lock():
                    self._restore_map(journal.get("preimages", {}))
                journal["state"] = TransactionState.ROLLED_BACK.value
            else:
                journal["state"] = TransactionState.REJECTED.value
            self._write_journal(journal)
            if isinstance(exc, (PatchValidationError, TransactionConflictError)):
                raise
            raise PatchValidationError(
                f"Delete transaction failed: {type(exc).__name__}: {exc}"
            ) from exc

    def rollback(self, transaction_id: str):
        """Compatibility alias for one-source compensating restore.

        Original change Journals remain committed and immutable in the state
        dimension; the returned receipt belongs to a new restore transaction.
        """
        from miniclaude.execution.recovery import RestoreConflictError, RestoreUnavailableError
        try:
            return self.restore_transactions((transaction_id,))
        except RestoreConflictError as exc:
            raise TransactionConflictError(str(exc)) from exc
        except RestoreUnavailableError as exc:
            raise TransactionConflictError(f"Transaction rollback data is no longer available: {exc}") from exc

    def finalize(self, transaction_id: str) -> TransactionReceipt:
        """Accept a committed change and make its backups eligible for GC."""
        with self._commit_lock():
            journal = self._read_journal(transaction_id)
            if journal["state"] != TransactionState.COMMITTED.value:
                raise TransactionConflictError("Only committed transactions can be finalized")
            if journal.get("pinned"):
                raise TransactionConflictError("Pinned transactions must be unpinned before finalizing")
            if not journal.get("finalized_at"):
                journal["finalized_at"] = self._utc_now().isoformat()
                journal["rollback_available"] = False
                journal["recoverability"] = "pending_prune"
                self._write_journal(journal)
            return self._receipt(journal)

    def set_pinned(self, transaction_id: str, pinned: bool) -> TransactionReceipt:
        with self._commit_lock():
            journal = self._read_journal(transaction_id)
            if journal.get("backup_expired_at") or journal.get("hot_backup_expired_at"):
                raise TransactionConflictError("Expired rollback data cannot be pinned")
            if pinned and journal.get("finalized_at"):
                raise TransactionConflictError("Finalized transactions cannot be pinned")
            journal["pinned"] = bool(pinned)
            self._write_journal(journal)
            return self._receipt(journal)

    def prune(
        self,
        *,
        dry_run: bool = True,
        policy: RetentionPolicy | None = None,
        now: datetime | None = None,
    ) -> PruneReport:
        """Expire eligible rollback windows and mark/sweep unreferenced blobs."""
        selected = policy or self.retention_policy
        instant = now or self._utc_now()
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        with self._commit_lock():
            journals = [json.loads(path.read_text(encoding="utf-8")) for path in self.journals.glob("*.json")]
            eligible: list[dict[str, Any]] = []
            protected: list[dict[str, Any]] = []
            for journal in journals:
                if journal.get("backup_expired_at"):
                    continue
                if self._eligible_for_expiry(journal, selected, instant):
                    eligible.append(journal)
                else:
                    protected.append(journal)
            eligible.sort(key=lambda item: (int(item.get("importance", 50)), self._state_timestamp(item)))

            live_digests: set[str] = set()
            for journal in protected:
                live_digests.update(self._journal_digests(journal))
            live_digests.update(self.restore_coordinator.leased_digests("warm"))
            live_chunks = self.backup_store.referenced_chunks(live_digests)

            orphan_cutoff = instant.timestamp() - selected.orphan_grace_hours * 3600
            blob_candidates = []
            blob_candidates.extend(
                path for path in self.blobs.iterdir()
                if path.is_file() and path.name not in live_digests and path.stat().st_mtime <= orphan_cutoff
            )
            blob_candidates.extend(
                path for path in self.manifests.iterdir()
                if path.is_file() and path.stem not in live_digests and path.stat().st_mtime <= orphan_cutoff
            )
            blob_candidates.extend(
                path for path in self.chunks.iterdir()
                if path.is_file() and path.stem not in live_chunks and path.stat().st_mtime <= orphan_cutoff
            )
            trash_cutoff = instant.timestamp() - selected.trash_grace_days * 86400
            trash_candidates = [
                path for path in self.trash.iterdir()
                if path.is_file() and path.stat().st_mtime <= trash_cutoff
            ]
            reclaimable = sum(path.stat().st_size for path in blob_candidates) + sum(
                path.stat().st_size for path in trash_candidates
            )
            released = 0
            if not dry_run:
                expired_at = instant.isoformat()
                for journal in eligible:
                    journal["backup_expired_at"] = expired_at
                    journal["rollback_available"] = False
                    journal["recoverability"] = "expired"
                    self._write_journal(journal)
                for path in blob_candidates:
                    destination = self.trash / f"{path.parent.name}-{path.name}.{uuid4().hex}.trash"
                    os.replace(path, destination)
                for directory in (self.blobs, self.manifests, self.chunks):
                    self._fsync_directory(directory)
                self._fsync_directory(self.trash)
                for path in trash_candidates:
                    size = path.stat().st_size
                    path.unlink()
                    released += size
                self._fsync_directory(self.trash)

            hot_deleted, hot_released = self._prune_hot_vault(
                instant=instant, dry_run=dry_run, force_transaction_ids=set()
            )
            storage = self._storage_bytes()
            return PruneReport(
                dry_run=dry_run,
                eligible_transactions=tuple(journal["transaction_id"] for journal in eligible),
                protected_transactions=tuple(journal["transaction_id"] for journal in protected),
                blobs_to_trash=tuple(path.name for path in blob_candidates),
                trash_to_delete=tuple(path.name for path in trash_candidates),
                reclaimable_bytes=reclaimable,
                released_bytes=released,
                storage_bytes=storage,
                over_budget_bytes=max(0, storage - selected.max_backup_bytes),
                hot_manifests_deleted=hot_deleted,
                hot_released_bytes=hot_released,
                hot_storage_bytes=self.hot_store.storage_bytes(),
            )

    def preview_hot_purge(self, transaction_ids: tuple[str, ...]) -> HotPurgePreview:
        """Bind an irreversible hot-vault purge to exact transactions and content."""
        canonical = tuple(sorted({str(UUID(value)) for value in transaction_ids}))
        if not canonical:
            raise ValueError("At least one transaction ID is required")
        digests: set[str] = set()
        for transaction_id in canonical:
            journal = self._read_journal(transaction_id)
            if self._is_active_state(journal):
                raise TransactionConflictError("Active or recovering transactions cannot be purged")
            for group in (journal.get("preimages", {}), journal.get("postimages", {})):
                for snapshot in group.values():
                    if snapshot.get("storage_tier") == "hot" and not snapshot.get("backup_expired"):
                        digest = snapshot.get("digest")
                        if isinstance(digest, str) and digest:
                            digests.add(digest)
        logical_bytes = sum(
            self.hot_store.original_size(digest)
            for digest in digests
            if self.hot_store.contains(digest)
        )
        payload = {
            "transaction_ids": canonical,
            "file_digests": sorted(digests),
            "logical_bytes": logical_bytes,
        }
        token = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return HotPurgePreview(
            transaction_ids=canonical,
            file_digests=tuple(payload["file_digests"]),
            logical_bytes=logical_bytes,
            purge_token=token,
        )

    def purge_hot_backups(
        self,
        transaction_ids: tuple[str, ...],
        *,
        purge_token: str,
    ) -> HotPurgePreview:
        """Permanently remove exact large-file recovery references after approval."""
        with self._commit_lock():
            preview = self.preview_hot_purge(transaction_ids)
            if not purge_token or purge_token != preview.purge_token:
                raise TransactionConflictError(
                    "Hot-cache purge scope changed; request a new preview and approval"
                )
            self._prune_hot_vault(
                instant=self._utc_now(),
                dry_run=False,
                force_transaction_ids=set(preview.transaction_ids),
            )
            return preview

    def recover_incomplete(self) -> tuple[TransactionReceipt, ...]:
        recovered = []
        with self._commit_lock():
            for path in self.journals.glob("*.json"):
                journal = json.loads(path.read_text(encoding="utf-8"))
                state = TransactionState(journal["state"])
                if state in {
                    TransactionState.COMMITTING, TransactionState.RECOVERING,
                    TransactionState.RECOVERY_CONFLICTED, TransactionState.ROLLING_BACK,
                }:
                    conflicts = self._recovery_conflicts(journal)
                    if conflicts:
                        journal["state"] = TransactionState.RECOVERY_CONFLICTED.value
                        journal["conflict_paths"] = conflicts
                        journal["recovery_reason"] = "live_content_matches_neither_preimage_nor_postimage"
                        self._write_journal(journal)
                        recovered.append(self._receipt(journal))
                        continue
                    journal["state"] = TransactionState.RECOVERING.value
                    self._write_journal(journal)
                    self._restore_map(journal.get("preimages", {}))
                    journal["state"] = TransactionState.ROLLED_BACK.value
                    journal["recovered"] = True
                    self._write_journal(journal)
                    recovered.append(self._receipt(journal))
                elif state in {TransactionState.PROPOSED, TransactionState.PREPARED, TransactionState.STAGED, TransactionState.VERIFIED}:
                    # No root mutation can precede COMMITTING, so an interrupted
                    # pre-commit transaction is safe to abandon without restoring.
                    journal["state"] = TransactionState.REJECTED.value
                    journal["recovered"] = True
                    journal["recovery_reason"] = "abandoned_before_commit"
                    self._write_journal(journal)
                    recovered.append(self._receipt(journal))
        return tuple(recovered)

    def preview_restore(self, transaction_ids: tuple[str, ...]):
        return self.restore_coordinator.preview(transaction_ids)

    def restore_transactions(
        self,
        transaction_ids: tuple[str, ...],
        *,
        requester: dict[str, Any] | None = None,
    ):
        return self.restore_coordinator.restore(transaction_ids, requester=requester)

    def transactions_for_task(self, task_id: str) -> tuple[TransactionReceipt, ...]:
        """Return committed transactions causally attributed to one task."""
        values = []
        for path in self.journals.glob("*.json"):
            journal = json.loads(path.read_text(encoding="utf-8"))
            causal = journal.get("causal", {})
            if causal.get("task_id") == task_id:
                values.append(journal)
        values.sort(key=self._state_timestamp)
        return tuple(self._receipt(value) for value in values)

    def recover_incomplete_restores(self):
        return self.restore_coordinator.recover_incomplete()

    def status(self, transaction_id: str) -> TransactionReceipt:
        return self._receipt(self._read_journal(transaction_id))

    def audit_recovery(self, transaction_id: str) -> RecoveryHealthReport:
        """Read and authenticate every required preimage without mutating the workspace."""
        journal = self._read_journal(transaction_id)
        checked: list[str] = []
        unavailable: list[str] = []
        corrupt: list[str] = []
        for relative, value in journal.get("preimages", {}).items():
            snapshot = self._snapshot(value)
            checked.append(relative)
            if snapshot.kind != "file" or not snapshot.digest:
                continue
            if value.get("backup_expired"):
                unavailable.append(relative)
                continue
            store = self.hot_store if snapshot.storage_tier == "hot" else self.backup_store
            try:
                if not store.contains(snapshot.digest):
                    unavailable.append(relative)
                    continue
                for _chunk in store.iter_file(snapshot.digest):
                    pass
            except (BackupStoreError, OSError, ValueError, json.JSONDecodeError):
                corrupt.append(relative)

        conflicts: list[str] = []
        if journal.get("state") == TransactionState.COMMITTED.value:
            for relative, value in journal.get("postimages", {}).items():
                expected = self._snapshot(value)
                if not self._same_content(
                    self._capture(self._workspace_path(relative), persist=False), expected
                ):
                    conflicts.append(relative)

        if corrupt:
            health = "corrupt"
            detail = f"Integrity verification failed for: {corrupt}"
        elif unavailable or journal.get("backup_expired_at") or journal.get("hot_backup_expired_at"):
            health = "expired"
            detail = f"Required recovery data is unavailable for: {unavailable}"
        elif conflicts or journal.get("state") == TransactionState.RECOVERY_CONFLICTED.value:
            health = "conflicted"
            detail = "Workspace content changed after the transaction"
        else:
            health = "available"
            detail = "All required preimages authenticated and live content is compatible"
        return RecoveryHealthReport(
            transaction_id=transaction_id,
            health=health,
            checked_paths=tuple(checked),
            unavailable_paths=tuple(unavailable + corrupt),
            conflict_paths=tuple(conflicts),
            detail=detail,
            checked_at=self._utc_now().isoformat(),
        )

    def list_recent(self, limit: int = 20) -> tuple[TransactionReceipt, ...]:
        paths = sorted(
            self.journals.glob("*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )[: max(0, limit)]
        return tuple(self._receipt(json.loads(path.read_text(encoding="utf-8"))) for path in paths)

    def storage_status(self) -> dict[str, Any]:
        result = self.backup_store.stats()
        maximum = self.retention_policy.max_backup_bytes
        used = int(result["storage_bytes"])
        result.update(
            capacity_bytes=maximum,
            available_bytes=max(0, maximum - used),
            over_budget_bytes=max(0, used - maximum),
            hot_vault={
                **self.hot_store.stats(),
                "threshold_bytes": self.hot_vault_policy.threshold_bytes,
                "ttl_hours": self.hot_vault_policy.ttl_hours,
                "capacity_bytes": self.hot_vault_policy.max_backup_bytes,
                "minimum_free_bytes": self.hot_vault_policy.minimum_free_bytes,
            },
        )
        return result

    def _create_stage(self, transaction_id: str) -> Path:
        base = Path(tempfile.mkdtemp(prefix=f"miniclaude_tx_{transaction_id[:8]}_"))
        stage = base / "workspace"
        shutil.copytree(self.root, stage, symlinks=True, ignore=shutil.ignore_patterns(*self.IGNORE))
        environment = sanitized_subprocess_env()
        subprocess.run(["git", "init", "-q"], cwd=stage, check=True, capture_output=True, env=environment)
        subprocess.run(["git", "add", "-A"], cwd=stage, check=True, capture_output=True, env=environment)
        return stage

    def _capture(self, path: Path, *, persist: bool = True) -> PathSnapshot:
        if path.is_symlink():
            return PathSnapshot("symlink", link_target=os.readlink(path))
        if not path.exists():
            return PathSnapshot("absent")
        if not path.is_file():
            raise PatchValidationError(f"Only file paths are transactional: {path}")
        file_stat = path.stat()
        size = file_stat.st_size
        tier = "hot" if size >= self.hot_vault_policy.threshold_bytes else "warm"
        expires_at = None
        if persist:
            with self._commit_lock():
                try:
                    if tier == "hot":
                        self._admit_hot_backup(size)
                        digest = self.hot_store.store_path(
                            path, max_bytes=self.hot_vault_policy.max_backup_bytes
                        )
                        expires_at = (
                            self._utc_now() + timedelta(hours=self.hot_vault_policy.ttl_hours)
                        ).isoformat()
                    else:
                        digest = self.backup_store.store_path(
                            path, max_bytes=self.retention_policy.max_backup_bytes
                        )
                except (BackupBudgetError, BackupStoreError) as exc:
                    raise PatchValidationError(str(exc)) from exc
        else:
            digest = self._hash_path(path)
        return PathSnapshot(
            "file",
            digest=digest,
            mode=stat.S_IMODE(file_stat.st_mode),
            storage_tier=tier if persist else None,
            backup_expires_at=expires_at,
        )

    def _restore(self, path: Path, snapshot: PathSnapshot) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise PatchValidationError(f"Restore target crosses workspace boundary: {path}") from exc
        if snapshot.kind == "absent":
            if path.is_symlink() or path.is_file():
                path.unlink()
                self._fsync_directory(path.parent)
            if path.exists() or path.is_symlink():
                raise PatchValidationError(f"Restore postcondition failed; path still exists: {path}")
            return
        if snapshot.kind == "symlink":
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            os.symlink(snapshot.link_target or "", temporary)
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
            if not path.is_symlink() or os.readlink(path) != (snapshot.link_target or ""):
                raise PatchValidationError(f"Restore postcondition failed for symlink: {path}")
            return
        if snapshot.kind != "file" or not snapshot.digest:
            raise PatchValidationError(f"Unknown snapshot kind: {snapshot.kind}")
        try:
            store = self.hot_store if snapshot.storage_tier == "hot" else self.backup_store
            if not store.contains(snapshot.digest):
                raise BackupStoreError(f"Required {snapshot.storage_tier or 'warm'} backup is unavailable")
        except (BackupStoreError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise PatchValidationError(f"Backup restore failed: {exc}") from exc
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                store.write_to(snapshot.digest, handle)
                handle.flush()
                os.fsync(handle.fileno())
            if snapshot.mode is not None:
                os.chmod(temporary, snapshot.mode)
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
            if self._hash_path(path) != snapshot.digest:
                raise PatchValidationError(f"Restore postcondition digest mismatch: {path}")
        finally:
            if temporary.exists():
                temporary.unlink()

    def _restore_map(self, values: dict[str, dict[str, Any]]) -> None:
        for relative, value in values.items():
            self._restore(self._workspace_path(relative), self._snapshot(value))

    def _recovery_conflicts(self, journal: dict[str, Any]) -> list[str]:
        conflicts = []
        preimages = journal.get("preimages", {})
        postimages = journal.get("postimages", {})
        for relative in journal.get("touched_paths", []):
            live = self._capture(self._workspace_path(relative), persist=False)
            expected = []
            if relative in preimages:
                expected.append(self._snapshot(preimages[relative]))
            if relative in postimages:
                expected.append(self._snapshot(postimages[relative]))
            if not expected or not any(self._same_content(live, item) for item in expected):
                conflicts.append(relative)
        return conflicts

    def _admit_hot_backup(self, logical_size: int) -> None:
        policy = self.hot_vault_policy
        used = self.hot_store.storage_bytes()
        if used + logical_size > policy.max_backup_bytes:
            raise BackupBudgetError(
                "Large-file hot backup budget would be exceeded; deletion was refused"
            )
        free = shutil.disk_usage(self.transaction_root).free
        if free - logical_size < policy.minimum_free_bytes:
            raise BackupBudgetError(
                "Insufficient free disk for a durable large-file backup; deletion was refused"
            )

    @staticmethod
    def _hash_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _snapshot(value: dict[str, Any]) -> PathSnapshot:
        return PathSnapshot(**{
            key: item for key, item in value.items()
            if key in PathSnapshot.__dataclass_fields__
        })

    @staticmethod
    def _same_content(left: PathSnapshot, right: PathSnapshot) -> bool:
        return (
            left.kind,
            left.digest,
            left.mode,
            left.link_target,
        ) == (
            right.kind,
            right.digest,
            right.mode,
            right.link_target,
        )

    @staticmethod
    def _journal_digests(journal: dict[str, Any], *, tier: str = "warm") -> set[str]:
        values: set[str] = set()
        for group in (journal.get("preimages", {}), journal.get("postimages", {})):
            for snapshot in group.values():
                snapshot_tier = snapshot.get("storage_tier") or "warm"
                if snapshot_tier != tier or snapshot.get("backup_expired"):
                    continue
                digest = snapshot.get("digest")
                if isinstance(digest, str) and digest:
                    values.add(digest)
        return values

    def _prune_hot_vault(
        self,
        *,
        instant: datetime,
        dry_run: bool,
        force_transaction_ids: set[str],
    ) -> tuple[tuple[str, ...], int]:
        """Expire 24-hour hot references, then directly unlink unreferenced data."""
        leased_hot = self.restore_coordinator.leased_digests("hot")
        live_digests: set[str] = set(leased_hot)
        journals = [json.loads(path.read_text(encoding="utf-8")) for path in self.journals.glob("*.json")]
        for journal in journals:
            force = journal.get("transaction_id") in force_transaction_ids
            journal_hot = self._journal_digests(journal, tier="hot")
            protected = (
                self._is_active_state(journal)
                or bool(journal.get("pinned"))
                or bool(journal_hot & leased_hot)
            )
            expired_preimage = False
            changed = False
            for group_name in ("preimages", "postimages"):
                for snapshot in journal.get(group_name, {}).values():
                    if snapshot.get("storage_tier") != "hot" or snapshot.get("backup_expired"):
                        continue
                    expires_at = snapshot.get("backup_expires_at")
                    ttl_expired = bool(
                        expires_at and self._parse_timestamp(str(expires_at)) <= instant
                    )
                    expire = force or (ttl_expired and not protected)
                    digest = snapshot.get("digest")
                    if expire:
                        if group_name == "preimages":
                            expired_preimage = True
                        if not dry_run:
                            snapshot["backup_expired"] = True
                            snapshot["backup_expired_at"] = instant.isoformat()
                            changed = True
                    elif isinstance(digest, str) and digest:
                        live_digests.add(digest)
            if expired_preimage and not dry_run:
                journal["hot_backup_expired_at"] = instant.isoformat()
                journal["rollback_available"] = False
                journal["recoverability"] = "expired"
                changed = True
            if changed:
                self._write_journal(journal)

        live_chunks = self.hot_store.referenced_chunks(live_digests)
        candidates = [
            path for path in self.hot_store.legacy_blobs.iterdir()
            if path.is_file() and path.name not in live_digests
        ]
        manifest_candidates = [
            path for path in self.hot_store.manifests.iterdir()
            if path.is_file() and path.stem not in live_digests
        ]
        candidates.extend(manifest_candidates)
        candidates.extend(
            path for path in self.hot_store.chunks.iterdir()
            if path.is_file() and path.stem not in live_chunks
        )
        released = sum(path.stat().st_size for path in candidates)
        if not dry_run:
            for path in candidates:
                path.unlink()
            for directory in (
                self.hot_store.legacy_blobs,
                self.hot_store.manifests,
                self.hot_store.chunks,
            ):
                self._fsync_directory(directory)
        return tuple(path.name for path in manifest_candidates), released if not dry_run else 0

    @staticmethod
    def _is_active_state(journal: dict[str, Any]) -> bool:
        return TransactionState(journal["state"]) in {
            TransactionState.PROPOSED,
            TransactionState.PREPARED,
            TransactionState.STAGED,
            TransactionState.VERIFIED,
            TransactionState.COMMITTING,
            TransactionState.RECOVERING,
            TransactionState.RECOVERY_CONFLICTED,
            TransactionState.ROLLING_BACK,
        }

    def _eligible_for_expiry(
        self,
        journal: dict[str, Any],
        policy: RetentionPolicy,
        now: datetime,
    ) -> bool:
        state = TransactionState(journal["state"])
        if self._is_active_state(journal) or journal.get("pinned"):
            return False
        if journal.get("finalized_at"):
            return True
        age = now - self._state_timestamp(journal)
        retention_days = policy.committed_days if state == TransactionState.COMMITTED else policy.terminal_days
        return age.total_seconds() >= retention_days * 86400

    @staticmethod
    def _infer_importance(paths: tuple[str, ...]) -> int:
        """Conservative file importance used only to order already-eligible eviction."""
        score = 0
        for raw in paths:
            path = raw.lower()
            name = Path(path).name
            if name in {"pyproject.toml", "package.json", "cargo.toml", "go.mod"} or "migration" in path:
                score = max(score, 90)
            elif any(part in path for part in ("auth", "security", "payment", "src/", "miniclaude/")):
                score = max(score, 75)
            elif path.startswith("tests/") or "/tests/" in path:
                score = max(score, 45)
            elif path.startswith("docs/") or path.endswith((".md", ".txt")):
                score = max(score, 35)
            elif any(part in path for part in ("generated/", "dist/", "build/", "coverage")):
                score = max(score, 15)
            else:
                score = max(score, 50)
        return score or 50

    def _state_timestamp(self, journal: dict[str, Any]) -> datetime:
        state = str(journal.get("state", ""))
        for transition in reversed(journal.get("transitions", [])):
            if transition.get("to") == state and transition.get("at"):
                return self._parse_timestamp(str(transition["at"]))
        fallback = str(journal.get("created_at") or self._utc_now().isoformat())
        return self._parse_timestamp(fallback)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _storage_bytes(self) -> int:
        return self.backup_store.storage_bytes()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _write_journal(self, value: dict[str, Any]) -> None:
        path = self._journal_path(str(value["transaction_id"]))
        now = self._utc_now().isoformat()
        value.setdefault("created_at", now)
        value["updated_at"] = now
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            history = list(previous.get("transitions", []))
            if previous.get("state") != value.get("state"):
                history.append({"from": previous.get("state", ""), "to": value.get("state", ""), "at": now})
            value["transitions"] = history
        else:
            value.setdefault("transitions", [{"from": "", "to": value.get("state", ""), "at": now}])
        temporary = path.with_suffix(".tmp")
        self._durable_write(
            temporary,
            json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        os.replace(temporary, path)
        self._fsync_directory(path.parent)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _read_journal(self, transaction_id: str) -> dict[str, Any]:
        return json.loads(self._journal_path(transaction_id).read_text(encoding="utf-8"))

    def _journal_path(self, transaction_id: str) -> Path:
        try:
            canonical = str(UUID(transaction_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Invalid transaction ID") from exc
        if canonical != transaction_id.lower():
            raise ValueError("Invalid transaction ID")
        return self.journals / f"{canonical}.json"

    def _workspace_path(self, relative: str) -> Path:
        if not relative or "\\" in relative:
            raise PatchValidationError(f"Invalid transactional path: {relative}")
        candidate = self.root.joinpath(*relative.split("/"))
        try:
            candidate.parent.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise PatchValidationError(f"Transactional path crosses a workspace symlink: {relative}") from exc
        return candidate

    @contextmanager
    def _commit_lock(self, timeout: float = 10.0):
        """Serialize conflict-check + commit across processes without dependencies."""
        lock_path = self.transaction_root / "commit.lock"
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TransactionConflictError("Workspace transaction lock is busy") from exc
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _durable_write(path: Path, content: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _receipt(journal: dict[str, Any]) -> TransactionReceipt:
        state = TransactionState(journal["state"])
        if journal.get("recoverability"):
            recoverability = str(journal["recoverability"])
        elif state == TransactionState.COMMITTED:
            recoverability = "available"
        elif state in {
            TransactionState.PROPOSED, TransactionState.PREPARED, TransactionState.STAGED,
            TransactionState.VERIFIED, TransactionState.COMMITTING, TransactionState.RECOVERING,
            TransactionState.RECOVERY_CONFLICTED, TransactionState.ROLLING_BACK,
        }:
            recoverability = "required"
        elif state == TransactionState.ROLLED_BACK:
            recoverability = "completed"
        else:
            recoverability = "not_applicable"
        return TransactionReceipt(
            transaction_id=journal["transaction_id"], patch_id=journal["patch_id"],
            state=state, touched_paths=tuple(journal["touched_paths"]),
            verification=tuple((str(item[0]), int(item[1])) for item in journal.get("verification", [])),
            conflict_paths=tuple(journal.get("conflict_paths", [])), recovered=bool(journal.get("recovered", False)),
            created_at=str(journal.get("created_at", "")), updated_at=str(journal.get("updated_at", "")),
            transitions=tuple(
                (str(item.get("from", "")), str(item.get("to", "")), str(item.get("at", "")))
                for item in journal.get("transitions", [])
            ),
            pinned=bool(journal.get("pinned", False)),
            rollback_available=(
                journal.get("state") == TransactionState.COMMITTED.value
                and not journal.get("finalized_at")
                and not journal.get("backup_expired_at")
                and not journal.get("hot_backup_expired_at")
            ),
            recoverability=recoverability,
            finalized_at=str(journal.get("finalized_at", "")),
            backup_expired_at=str(journal.get("backup_expired_at", "")),
            hot_backup_expired_at=str(journal.get("hot_backup_expired_at", "")),
            importance=int(journal.get("importance", 50)),
            causal=dict(journal.get("causal", {})),
        )

    @staticmethod
    def _normalize_causal(value: dict[str, Any] | None) -> dict[str, Any]:
        if not value:
            return {}
        allowed = {
            "session_id", "turn_id", "step_id", "agent_id", "task_id",
            "parent_task_id", "worktree_id", "baseline", "declared_read_paths",
            "declared_write_paths", "dependency_task_ids",
        }
        result: dict[str, Any] = {}
        for key in allowed:
            item = value.get(key)
            if isinstance(item, str):
                result[key] = item[:4096]
            elif isinstance(item, (list, tuple)):
                result[key] = [str(part)[:4096] for part in item[:256]]
        return result
