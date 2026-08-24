"""Leased, preflighted and crash-recoverable compensating restore transactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from miniclaude.execution.backup_store import BackupStoreError
from miniclaude.execution.patches import PatchValidationError

if TYPE_CHECKING:
    from miniclaude.execution.transactions import DurableChangeEngine, PathSnapshot


class RestoreState(StrEnum):
    REQUESTED = "requested"
    PREFLIGHTING = "preflighting"
    READY = "ready"
    MATERIALIZING = "materializing"
    VERIFIED = "verified"
    COMMITTING = "committing"
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    CONFLICTED = "conflicted"
    CORRUPT = "corrupt"
    FAILED = "failed"
    RECOVERING = "recovering"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class RestorePreview:
    source_transaction_ids: tuple[str, ...]
    touched_paths: tuple[str, ...]
    required_warm_digests: tuple[str, ...]
    required_hot_digests: tuple[str, ...]
    materialization_bytes: int
    health: str
    conflicts: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    restore_id: str
    state: RestoreState
    source_transaction_ids: tuple[str, ...]
    touched_paths: tuple[str, ...]
    created_at: str
    updated_at: str
    recovered: bool = False
    error: str = ""


class RestoreConflictError(RuntimeError):
    pass


class RestoreUnavailableError(RuntimeError):
    pass


class RestoreCoordinator:
    """Create compensating restores without mutating original transaction history."""

    TERMINAL = {
        RestoreState.COMPLETED.value,
        RestoreState.UNAVAILABLE.value,
        RestoreState.CORRUPT.value,
        RestoreState.FAILED.value,
        RestoreState.ABORTED.value,
    }

    def __init__(self, engine: DurableChangeEngine) -> None:
        self.engine = engine
        self.root = engine.transaction_root / "restores"
        self.journals = self.root / "journals"
        self.leases = self.root / "leases"
        self.staging = self.root / "staging"
        for directory in (self.root, self.journals, self.leases, self.staging):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass

    def preview(
        self,
        transaction_ids: tuple[str, ...],
        *,
        live_cache: dict[str, PathSnapshot] | None = None,
    ) -> RestorePreview:
        with self.engine._commit_lock():
            plan = self._build_plan(transaction_ids)
            conflicts = self._live_conflicts(plan["expected"], live_cache=live_cache)
            unavailable = self._missing_dependencies(plan)
        health = "conflicted" if conflicts else "unavailable" if unavailable else "available"
        return RestorePreview(
            source_transaction_ids=tuple(plan["transaction_ids"]),
            touched_paths=tuple(plan["targets"]),
            required_warm_digests=tuple(sorted(plan["warm_digests"])),
            required_hot_digests=tuple(sorted(plan["hot_digests"])),
            materialization_bytes=int(plan["materialization_bytes"]),
            health=health,
            conflicts=tuple(conflicts),
            unavailable=tuple(unavailable),
        )

    def restore(self, transaction_ids: tuple[str, ...]) -> RestoreReceipt:
        restore_id = str(uuid4())
        journal: dict[str, Any] = {
            "schema_version": 1,
            "restore_id": restore_id,
            "state": RestoreState.REQUESTED.value,
            "source_transaction_ids": list(transaction_ids),
            "touched_paths": [],
            "committed_paths": [],
            "recovered": False,
        }
        self._write_journal(journal)
        mutation_started = False
        stage = self.staging / restore_id
        try:
            journal["state"] = RestoreState.PREFLIGHTING.value
            self._write_journal(journal)
            with self.engine._commit_lock():
                plan = self._build_plan(transaction_ids)
                conflicts = self._live_conflicts(plan["expected"])
                if conflicts:
                    raise RestoreConflictError(f"Restore would overwrite newer content: {conflicts}")
                unavailable = self._missing_dependencies(plan)
                if unavailable:
                    raise RestoreUnavailableError(f"Required recovery data is unavailable: {unavailable}")
                self._check_materialization_space(int(plan["materialization_bytes"]))
                journal.update(
                    source_transaction_ids=plan["transaction_ids"],
                    touched_paths=list(plan["targets"]),
                    targets=plan["targets"],
                    expected=plan["expected"],
                    state=RestoreState.READY.value,
                )
                self._write_lease(restore_id, plan)
                self._write_journal(journal)

            journal["state"] = RestoreState.MATERIALIZING.value
            self._write_journal(journal)
            self._authenticate_dependencies(plan)
            stage.mkdir(parents=True, exist_ok=True)
            materialized = self._materialize_targets(stage, plan["targets"])
            journal["materialized"] = materialized
            journal["state"] = RestoreState.VERIFIED.value
            self._write_journal(journal)

            with self.engine._commit_lock():
                conflicts = self._live_conflicts(plan["expected"])
                if conflicts:
                    raise RestoreConflictError(
                        f"Workspace changed after restore preflight: {conflicts}"
                    )
                journal["state"] = RestoreState.COMMITTING.value
                self._write_journal(journal)
                mutation_started = True
                for relative, snapshot_value in plan["targets"].items():
                    self._install_target(
                        relative,
                        snapshot_value,
                        materialized.get(relative),
                    )
                    journal["committed_paths"].append(relative)
                    self._write_journal(journal)
                journal["state"] = RestoreState.COMPLETED.value
                self._write_journal(journal)
            return self._receipt(journal)
        except Exception as exc:
            journal["error"] = f"{type(exc).__name__}: {exc}"
            if mutation_started:
                journal["state"] = RestoreState.RECOVERING.value
                self._write_journal(journal)
                try:
                    with self.engine._commit_lock():
                        self.engine._restore_map(journal.get("expected", {}))
                    journal["recovered"] = True
                    journal["state"] = RestoreState.FAILED.value
                except Exception as recovery_exc:
                    journal["state"] = RestoreState.CONFLICTED.value
                    journal["recovery_error"] = f"{type(recovery_exc).__name__}: {recovery_exc}"
            elif isinstance(exc, RestoreConflictError):
                journal["state"] = RestoreState.CONFLICTED.value
            elif isinstance(exc, RestoreUnavailableError):
                journal["state"] = RestoreState.UNAVAILABLE.value
            elif isinstance(exc, (BackupStoreError, json.JSONDecodeError)):
                journal["state"] = RestoreState.CORRUPT.value
            else:
                journal["state"] = RestoreState.ABORTED.value
            self._write_journal(journal)
            if isinstance(exc, (RestoreConflictError, RestoreUnavailableError, BackupStoreError)):
                raise
            raise PatchValidationError(f"Restore transaction failed: {journal['error']}") from exc
        finally:
            if journal.get("state") != RestoreState.CONFLICTED.value:
                self._release_lease(restore_id)
                shutil.rmtree(stage, ignore_errors=True)

    def recover_incomplete(self) -> tuple[RestoreReceipt, ...]:
        recovered: list[RestoreReceipt] = []
        for path in self.journals.glob("*.json"):
            journal = json.loads(path.read_text(encoding="utf-8"))
            state = RestoreState(journal["state"])
            restore_id = str(journal["restore_id"])
            if state.value in self.TERMINAL:
                self._release_lease(restore_id)
                shutil.rmtree(self.staging / restore_id, ignore_errors=True)
                continue
            if state == RestoreState.CONFLICTED:
                continue
            if state in {
                RestoreState.REQUESTED,
                RestoreState.PREFLIGHTING,
                RestoreState.READY,
                RestoreState.MATERIALIZING,
                RestoreState.VERIFIED,
            }:
                journal["state"] = RestoreState.ABORTED.value
                journal["recovered"] = True
                journal["error"] = "Interrupted before workspace mutation"
                self._write_journal(journal)
                self._release_lease(restore_id)
                shutil.rmtree(self.staging / restore_id, ignore_errors=True)
                recovered.append(self._receipt(journal))
                continue
            if state in {RestoreState.COMMITTING, RestoreState.RECOVERING}:
                targets = journal.get("targets", {})
                expected = journal.get("expected", {})
                conflicts = self._recovery_conflicts(targets, expected)
                if conflicts:
                    journal["state"] = RestoreState.CONFLICTED.value
                    journal["error"] = f"Unknown live content during crash recovery: {conflicts}"
                    self._write_journal(journal)
                    recovered.append(self._receipt(journal))
                    continue
                try:
                    with self.engine._commit_lock():
                        self.engine._restore_map(expected)
                    journal["state"] = RestoreState.ABORTED.value
                    journal["recovered"] = True
                    journal["error"] = "Interrupted restore compensated to pre-restore state"
                    self._write_journal(journal)
                    self._release_lease(restore_id)
                    shutil.rmtree(self.staging / restore_id, ignore_errors=True)
                except Exception as recovery_exc:
                    # Startup must remain available for diagnosis even when the
                    # compensation data is damaged. Keep the lease so pruning
                    # cannot make the incident less recoverable.
                    journal["state"] = RestoreState.CONFLICTED.value
                    journal["error"] = "Crash recovery compensation failed"
                    journal["recovery_error"] = (
                        f"{type(recovery_exc).__name__}: {recovery_exc}"
                    )
                    self._write_journal(journal)
                recovered.append(self._receipt(journal))
        self._remove_orphan_leases()
        return tuple(recovered)

    def status(self, restore_id: str) -> RestoreReceipt:
        return self._receipt(self._read_journal(restore_id))

    def abort(self, restore_id: str) -> RestoreReceipt:
        """Release a conflicted restore lease after an explicit operator decision."""
        with self.engine._commit_lock():
            journal = self._read_journal(restore_id)
            if journal.get("state") != RestoreState.CONFLICTED.value:
                raise RestoreConflictError("Only conflicted restores can be explicitly aborted")
            journal["state"] = RestoreState.ABORTED.value
            journal["error"] = "Conflicted restore explicitly aborted"
            self._write_journal(journal)
            self._release_lease(restore_id)
            shutil.rmtree(self.staging / restore_id, ignore_errors=True)
            return self._receipt(journal)

    def list_recent(self, limit: int = 20) -> tuple[RestoreReceipt, ...]:
        paths = sorted(
            self.journals.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True
        )[:max(0, limit)]
        return tuple(self._receipt(json.loads(path.read_text(encoding="utf-8"))) for path in paths)

    def leased_digests(self, tier: str) -> set[str]:
        result: set[str] = set()
        key = "hot_digests" if tier == "hot" else "warm_digests"
        for path in self.leases.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            result.update(item for item in value.get(key, []) if isinstance(item, str))
        return result

    def _build_plan(self, transaction_ids: tuple[str, ...]) -> dict[str, Any]:
        canonical = {str(UUID(value)) for value in transaction_ids}
        if not canonical:
            raise ValueError("At least one source transaction is required")
        sources = [self.engine._read_journal(value) for value in canonical]
        sources.sort(key=self.engine._state_timestamp, reverse=True)
        targets: dict[str, dict[str, Any]] = {}
        expected: dict[str, dict[str, Any]] = {}
        newer_preimage: dict[str, dict[str, Any]] = {}
        for source in sources:
            if source.get("state") != "committed":
                raise RestoreUnavailableError(
                    f"Source transaction is not committed: {source['transaction_id']}"
                )
            if source.get("finalized_at") or source.get("backup_expired_at") or source.get("hot_backup_expired_at"):
                raise RestoreUnavailableError(
                    f"Source transaction recovery data expired: {source['transaction_id']}"
                )
            for relative in source.get("touched_paths", []):
                postimage = source["postimages"][relative]
                preimage = source["preimages"][relative]
                if relative not in expected:
                    expected[relative] = postimage
                elif not self.engine._same_content(
                    self.engine._snapshot(newer_preimage[relative]),
                    self.engine._snapshot(postimage),
                ):
                    raise RestoreConflictError(
                        f"Transactions are not contiguous for path: {relative}"
                    )
                targets[relative] = preimage
                newer_preimage[relative] = preimage
        warm: set[str] = set()
        hot: set[str] = set()
        materialization_bytes = 0
        for group in (targets, expected):
            for snapshot in group.values():
                digest = snapshot.get("digest")
                if not isinstance(digest, str) or not digest:
                    continue
                (hot if snapshot.get("storage_tier") == "hot" else warm).add(digest)
        for snapshot in targets.values():
            digest = snapshot.get("digest")
            if isinstance(digest, str) and digest:
                store = self.engine.hot_store if snapshot.get("storage_tier") == "hot" else self.engine.backup_store
                if store.contains(digest):
                    materialization_bytes += store.original_size(digest)
        return {
            "transaction_ids": [source["transaction_id"] for source in sources],
            "targets": targets,
            "expected": expected,
            "warm_digests": warm,
            "hot_digests": hot,
            "materialization_bytes": materialization_bytes,
        }

    def _missing_dependencies(self, plan: dict[str, Any]) -> list[str]:
        missing = []
        for tier, store in (("warm", self.engine.backup_store), ("hot", self.engine.hot_store)):
            for digest in plan[f"{tier}_digests"]:
                if not store.contains(digest):
                    missing.append(f"{tier}:{digest}")
        return missing

    def _authenticate_dependencies(self, plan: dict[str, Any]) -> None:
        target_digests = {"warm": set(), "hot": set()}
        for snapshot in plan["targets"].values():
            digest = snapshot.get("digest")
            if isinstance(digest, str) and digest:
                tier = "hot" if snapshot.get("storage_tier") == "hot" else "warm"
                target_digests[tier].add(digest)
        for tier, store in (("warm", self.engine.backup_store), ("hot", self.engine.hot_store)):
            for digest in plan[f"{tier}_digests"]:
                if digest in target_digests[tier]:
                    continue
                for _chunk in store.iter_file(digest):
                    pass

    def _materialize_targets(
        self, stage: Path, targets: dict[str, dict[str, Any]]
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for relative, value in targets.items():
            snapshot = self.engine._snapshot(value)
            if snapshot.kind != "file" or not snapshot.digest:
                continue
            output = stage / f"{hashlib.sha256(relative.encode('utf-8')).hexdigest()}.file"
            store = self.engine.hot_store if snapshot.storage_tier == "hot" else self.engine.backup_store
            with output.open("wb") as handle:
                store.write_to(snapshot.digest, handle)
                handle.flush()
                os.fsync(handle.fileno())
            if self.engine._hash_path(output) != snapshot.digest:
                raise BackupStoreError(f"Materialized restore digest mismatch: {relative}")
            result[relative] = str(output)
        return result

    def _live_conflicts(
        self,
        expected: dict[str, dict[str, Any]],
        *,
        live_cache: dict[str, PathSnapshot] | None = None,
    ) -> list[str]:
        conflicts = []
        for relative, value in expected.items():
            if live_cache is not None and relative in live_cache:
                live = live_cache[relative]
            else:
                live = self.engine._capture(
                    self.engine._workspace_path(relative), persist=False
                )
                if live_cache is not None:
                    live_cache[relative] = live
            if not self.engine._same_content(live, self.engine._snapshot(value)):
                conflicts.append(relative)
        return conflicts

    def _install_target(
        self,
        relative: str,
        snapshot_value: dict[str, Any],
        materialized_path: str | None,
    ) -> None:
        snapshot = self.engine._snapshot(snapshot_value)
        target = self.engine._workspace_path(relative)
        if snapshot.kind != "file":
            self.engine._restore(target, snapshot)
            return
        if not materialized_path or not snapshot.digest:
            raise BackupStoreError(f"Verified restore materialization is missing: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.restore")
        try:
            with Path(materialized_path).open("rb") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if snapshot.mode is not None:
                os.chmod(temporary, snapshot.mode)
            os.replace(temporary, target)
            self.engine._fsync_directory(target.parent)
            if self.engine._hash_path(target) != snapshot.digest:
                raise BackupStoreError(f"Installed restore digest mismatch: {relative}")
        finally:
            if temporary.exists():
                temporary.unlink()

    def _recovery_conflicts(
        self,
        targets: dict[str, dict[str, Any]],
        expected: dict[str, dict[str, Any]],
    ) -> list[str]:
        conflicts: list[str] = []
        for relative in set(targets) | set(expected):
            live = self.engine._capture(self.engine._workspace_path(relative), persist=False)
            choices = [
                self.engine._snapshot(group[relative])
                for group in (targets, expected)
                if relative in group
            ]
            if not any(self.engine._same_content(live, item) for item in choices):
                conflicts.append(relative)
        return conflicts

    def _check_materialization_space(self, required: int) -> None:
        reserve = self.engine.hot_vault_policy.minimum_free_bytes
        if shutil.disk_usage(self.root).free - required < reserve:
            raise RestoreUnavailableError(
                "Insufficient free disk to materialize and verify the complete restore"
            )

    def _write_lease(self, restore_id: str, plan: dict[str, Any]) -> None:
        value = {
            "restore_id": restore_id,
            "warm_digests": sorted(plan["warm_digests"]),
            "hot_digests": sorted(plan["hot_digests"]),
            "created_at": self._now(),
        }
        self._atomic_json(self.leases / f"{restore_id}.json", value)

    def _release_lease(self, restore_id: str) -> None:
        path = self.leases / f"{restore_id}.json"
        if path.exists():
            path.unlink()
            self.engine._fsync_directory(path.parent)

    def _remove_orphan_leases(self) -> None:
        for path in self.leases.glob("*.json"):
            try:
                restore_id = path.stem
                journal = self._read_journal(restore_id)
                if journal.get("state") in self.TERMINAL:
                    self._release_lease(restore_id)
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    def _write_journal(self, value: dict[str, Any]) -> None:
        now = self._now()
        value.setdefault("created_at", now)
        value["updated_at"] = now
        self._atomic_json(self._journal_path(str(value["restore_id"])), value)

    def _atomic_json(self, path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        self.engine._durable_write(
            temporary,
            json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        os.replace(temporary, path)
        self.engine._fsync_directory(path.parent)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _read_journal(self, restore_id: str) -> dict[str, Any]:
        return json.loads(self._journal_path(restore_id).read_text(encoding="utf-8"))

    def _journal_path(self, restore_id: str) -> Path:
        canonical = str(UUID(restore_id))
        if canonical != restore_id.lower():
            raise ValueError("Invalid restore ID")
        return self.journals / f"{canonical}.json"

    @staticmethod
    def _receipt(value: dict[str, Any]) -> RestoreReceipt:
        return RestoreReceipt(
            restore_id=str(value["restore_id"]),
            state=RestoreState(value["state"]),
            source_transaction_ids=tuple(value.get("source_transaction_ids", [])),
            touched_paths=tuple(value.get("touched_paths", [])),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            recovered=bool(value.get("recovered", False)),
            error=str(value.get("error", "")),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
