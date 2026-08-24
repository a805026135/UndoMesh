"""Transactional patch proposal tool."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from miniclaude.core.config import MiniClaudeConfig
from miniclaude.execution.patches import PatchSet, PatchValidationError
from miniclaude.execution.transactions import DurableChangeEngine, TransactionConflictError, TransactionState
from miniclaude.execution.backup_store import BackupStoreError
from miniclaude.execution.recovery import RestoreConflictError, RestoreUnavailableError
from miniclaude.execution.context import current_causal_metadata
from miniclaude.tools.base import BaseTool, ToolResult


def _engine(config: MiniClaudeConfig) -> DurableChangeEngine:
    return DurableChangeEngine(
        config.effective_project_dir(),
        retention_policy=config.transaction_retention_policy(),
        backup_encryption=config.transaction_backup_encryption,
        backup_encryption_key=config.transaction_backup_key_bytes(),
        chunking_policy=config.transaction_chunking_policy(),
        hot_vault_policy=config.transaction_hot_vault_policy(),
    )


class ApplyPatchSetTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "apply_patch_set"

    @property
    def description(self) -> str:
        return "Validate and transactionally apply a unified Git patch, rolling it back if verification fails."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "patch": {"type": "string", "description": "Unified diff"},
            "verification_commands": {"type": "array", "items": {"type": "string"}, "description": "Allowlisted verification commands"},
        }

    def _get_required_params(self) -> list[str]:
        return ["patch"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            patch = PatchSet.parse(str(kwargs.get("patch", "")))
            commands = tuple(str(item) for item in kwargs.get("verification_commands", []))
            receipt = _engine(self._config).apply(
                patch,
                verify=commands,
                causal=current_causal_metadata(),
            )
        except (PatchValidationError, TransactionConflictError, ValueError, OSError) as exc:
            return self._error(f"Patch rejected: {exc}", metadata={"denied": True})
        payload = {
            "transaction_id": receipt.transaction_id,
            "patch_id": receipt.patch_id,
            "touched_paths": receipt.touched_paths,
            "state": receipt.state.value,
            "verification": receipt.verification,
            "conflict_paths": receipt.conflict_paths,
        }
        if receipt.state != TransactionState.COMMITTED:
            return self._error(f"Patch was not committed: {receipt.state.value}", metadata=payload)
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class RollbackChangeSetTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "rollback_change_set"

    @property
    def description(self) -> str:
        return "Safely roll back a committed change transaction if its files have no newer edits."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"transaction_id": {"type": "string", "description": "Committed transaction ID"}}

    def _get_required_params(self) -> list[str]:
        return ["transaction_id"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            receipt = _engine(self._config).restore_transactions(
                (str(kwargs.get("transaction_id", "")),),
                requester=current_causal_metadata(),
            )
        except (BackupStoreError, RestoreConflictError, RestoreUnavailableError, TransactionConflictError, OSError, ValueError) as exc:
            return self._error(f"Rollback rejected: {exc}", metadata={"denied": True})
        payload = {
            "restore_id": receipt.restore_id,
            "source_transaction_ids": receipt.source_transaction_ids,
            "state": receipt.state.value,
            "touched_paths": receipt.touched_paths,
        }
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class PreviewRestoreTransactionsTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "preview_restore_transactions"

    @property
    def description(self) -> str:
        return "Preflight multiple change transactions without writing the workspace."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"transaction_ids": {"type": "array", "items": {"type": "string"}}}

    def _get_required_params(self) -> list[str]:
        return ["transaction_ids"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            ids = tuple(str(item) for item in kwargs.get("transaction_ids", []))
            preview = _engine(self._config).preview_restore(ids)
        except (BackupStoreError, RestoreConflictError, RestoreUnavailableError, OSError, ValueError) as exc:
            return self._error(f"Restore preview failed: {exc}")
        payload = asdict(preview)
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class RestoreTransactionsTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "restore_transactions"

    @property
    def description(self) -> str:
        return "Restore a contiguous set of committed transactions through one leased compensating transaction."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"transaction_ids": {"type": "array", "items": {"type": "string"}}}

    def _get_required_params(self) -> list[str]:
        return ["transaction_ids"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            ids = tuple(str(item) for item in kwargs.get("transaction_ids", []))
            receipt = _engine(self._config).restore_transactions(
                ids,
                requester=current_causal_metadata(),
            )
        except (BackupStoreError, RestoreConflictError, RestoreUnavailableError, OSError, ValueError) as exc:
            return self._error(f"Restore rejected: {exc}", metadata={"denied": True})
        payload = asdict(receipt)
        payload["state"] = receipt.state.value
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class RestoreTransactionStatusTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "restore_transaction_status"

    @property
    def description(self) -> str:
        return "Inspect one compensating restore transaction or list recent restores."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"restore_id": {"type": "string"}}

    async def execute(self, **kwargs: Any) -> ToolResult:
        coordinator = _engine(self._config).restore_coordinator
        try:
            restore_id = str(kwargs.get("restore_id", "")).strip()
            receipts = (coordinator.status(restore_id),) if restore_id else coordinator.list_recent()
        except (OSError, ValueError, KeyError) as exc:
            return self._error(f"Restore lookup failed: {exc}")
        payload = [{**asdict(item), "state": item.state.value} for item in receipts]
        return self._success(json.dumps(payload, ensure_ascii=False), metadata={"count": len(payload)})


class AbortRestoreTransactionTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "abort_restore_transaction"

    @property
    def description(self) -> str:
        return "Abort a conflicted restore and release its backup lease after human review."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"restore_id": {"type": "string"}}

    def _get_required_params(self) -> list[str]:
        return ["restore_id"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            receipt = _engine(self._config).restore_coordinator.abort(
                str(kwargs.get("restore_id", ""))
            )
        except (RestoreConflictError, OSError, ValueError) as exc:
            return self._error(f"Restore abort rejected: {exc}", metadata={"denied": True})
        payload = {**asdict(receipt), "state": receipt.state.value}
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class DeleteFilesTransactionallyTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "delete_files_transactionally"

    @property
    def description(self) -> str:
        return (
            "Delete workspace files only after a durable encrypted backup is verified; "
            "large files use the separate 24-hour hot recovery vault."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace-relative file paths",
            }
        }

    def _get_required_params(self) -> list[str]:
        return ["paths"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            paths = tuple(str(item) for item in kwargs.get("paths", []))
            receipt = _engine(self._config).delete_files(
                paths,
                causal=current_causal_metadata(),
            )
        except (PatchValidationError, TransactionConflictError, OSError, ValueError) as exc:
            return self._error(f"Delete rejected: {exc}", metadata={"denied": True})
        payload = {
            "transaction_id": receipt.transaction_id,
            "state": receipt.state.value,
            "touched_paths": receipt.touched_paths,
            "recoverability": receipt.recoverability,
        }
        if receipt.state != TransactionState.COMMITTED:
            return self._error(f"Files were not deleted: {receipt.state.value}", metadata=payload)
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class ChangeTransactionStatusTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "change_transaction_status"

    @property
    def description(self) -> str:
        return "Inspect one durable change transaction or list recent transaction receipts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"transaction_id": {"type": "string", "description": "Optional transaction ID"}}

    async def execute(self, **kwargs: Any) -> ToolResult:
        engine = _engine(self._config)
        try:
            transaction_id = str(kwargs.get("transaction_id", "")).strip()
            receipts = (engine.status(transaction_id),) if transaction_id else engine.list_recent()
        except (OSError, ValueError, KeyError) as exc:
            return self._error(f"Transaction lookup failed: {type(exc).__name__}")
        payload = [
            {
                "transaction_id": item.transaction_id,
                "patch_id": item.patch_id,
                "state": item.state.value,
                "touched_paths": item.touched_paths,
                "verification": item.verification,
                "conflict_paths": item.conflict_paths,
                "recovered": item.recovered,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "transitions": item.transitions,
                "pinned": item.pinned,
                "rollback_available": item.rollback_available,
                "recoverability": item.recoverability,
                "finalized_at": item.finalized_at,
                "backup_expired_at": item.backup_expired_at,
                "hot_backup_expired_at": item.hot_backup_expired_at,
                "importance": item.importance,
            }
            for item in receipts
        ]
        return self._success(json.dumps(payload, ensure_ascii=False), metadata={"count": len(payload)})


class FinalizeChangeTransactionTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "finalize_change_transaction"

    @property
    def description(self) -> str:
        return "Accept a committed transaction and make its rollback backups eligible for later pruning."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"transaction_id": {"type": "string"}}

    def _get_required_params(self) -> list[str]:
        return ["transaction_id"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            receipt = _engine(self._config).finalize(str(kwargs.get("transaction_id", "")))
        except (TransactionConflictError, OSError, ValueError) as exc:
            return self._error(f"Finalize rejected: {exc}", metadata={"denied": True})
        payload = {
            "transaction_id": receipt.transaction_id,
            "rollback_available": receipt.rollback_available,
            "recoverability": receipt.recoverability,
            "finalized_at": receipt.finalized_at,
        }
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class PinChangeTransactionTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "pin_change_transaction"

    @property
    def description(self) -> str:
        return "Pin or unpin rollback data so retention pruning cannot expire it."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "transaction_id": {"type": "string"},
            "pinned": {"type": "boolean"},
        }

    def _get_required_params(self) -> list[str]:
        return ["transaction_id", "pinned"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            receipt = _engine(self._config).set_pinned(
                str(kwargs.get("transaction_id", "")), bool(kwargs.get("pinned"))
            )
        except (TransactionConflictError, OSError, ValueError) as exc:
            return self._error(f"Pin update rejected: {exc}", metadata={"denied": True})
        payload = {"transaction_id": receipt.transaction_id, "pinned": receipt.pinned}
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class PruneChangeTransactionsTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "prune_change_transactions"

    @property
    def description(self) -> str:
        return "Preview or execute policy-based, reference-safe transaction backup cleanup."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"dry_run": {"type": "boolean", "description": "Defaults to true; false performs cleanup"}}

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            report = _engine(self._config).prune(dry_run=bool(kwargs.get("dry_run", True)))
        except (TransactionConflictError, OSError, ValueError) as exc:
            return self._error(f"Prune failed: {exc}", metadata={"denied": True})
        payload = asdict(report)
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class TransactionStorageStatusTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "transaction_storage_status"

    @property
    def description(self) -> str:
        return "Inspect rollback buffer capacity, compression, deduplication, encryption and trash usage."

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            payload = _engine(self._config).storage_status()
        except (OSError, ValueError, BackupStoreError) as exc:
            return self._error(f"Storage status failed: {exc}")
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class AuditTransactionRecoveryTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "audit_transaction_recovery"

    @property
    def description(self) -> str:
        return "Authenticate all rollback preimages and detect live workspace conflicts without writing files."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"transaction_id": {"type": "string"}}

    def _get_required_params(self) -> list[str]:
        return ["transaction_id"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            report = _engine(self._config).audit_recovery(str(kwargs.get("transaction_id", "")))
        except (BackupStoreError, OSError, ValueError, KeyError) as exc:
            return self._error(f"Recovery audit failed: {exc}")
        payload = asdict(report)
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)


class PurgeLargeBackupCacheTool(BaseTool):
    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "purge_large_backup_cache"

    @property
    def description(self) -> str:
        return (
            "Preview or permanently purge exact transactions from the 24-hour large-file cache. "
            "Actual purge requires the preview token and fresh human approval."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "transaction_ids": {"type": "array", "items": {"type": "string"}},
            "dry_run": {"type": "boolean", "description": "Defaults to true"},
            "purge_token": {"type": "string", "description": "Exact token returned by preview"},
        }

    def _get_required_params(self) -> list[str]:
        return ["transaction_ids"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            transaction_ids = tuple(str(item) for item in kwargs.get("transaction_ids", []))
            engine = _engine(self._config)
            if bool(kwargs.get("dry_run", True)):
                preview = engine.preview_hot_purge(transaction_ids)
            else:
                preview = engine.purge_hot_backups(
                    transaction_ids,
                    purge_token=str(kwargs.get("purge_token", "")),
                )
        except (BackupStoreError, TransactionConflictError, OSError, ValueError) as exc:
            return self._error(f"Large-cache purge rejected: {exc}", metadata={"denied": True})
        payload = asdict(preview)
        payload["dry_run"] = bool(kwargs.get("dry_run", True))
        payload["permanent"] = not payload["dry_run"]
        return self._success(json.dumps(payload, ensure_ascii=False), metadata=payload)
