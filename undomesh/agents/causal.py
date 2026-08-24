"""Durable causal task graph and Git-backed multi-agent rollback workflow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Iterable

from undomesh.agents.contracts import TaskContract
from undomesh.execution.transactions import DurableChangeEngine
from undomesh.execution.worktrees import (
    CandidateReviewReceipt,
    GitWorktreeManager,
    WorktreeReceipt,
    WorktreeReviewWorkflow,
)


class CausalTaskState(StrEnum):
    PREPARED = "prepared"
    REVIEWED = "reviewed"
    MERGED = "merged"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CausalTaskRecord:
    task_id: str
    agent_id: str
    parent_task_id: str
    state: CausalTaskState
    dependencies: tuple[str, ...]
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    transaction_ids: tuple[str, ...]
    worktree_id: str
    baseline: str
    candidate_commit: str
    candidate_tree: str
    review_sha256: str
    merge_commit: str
    rollback_commit: str


@dataclass(frozen=True, slots=True)
class CausalRollbackPlan:
    root_task_id: str
    strategy: str
    affected_task_ids: tuple[str, ...]
    rollback_task_ids: tuple[str, ...]
    replay_task_ids: tuple[str, ...]
    transaction_ids: tuple[str, ...]
    merge_commits: tuple[str, ...]
    plan_sha256: str


@dataclass(frozen=True, slots=True)
class CausalRollbackCandidate:
    plan: CausalRollbackPlan
    review: CandidateReviewReceipt


class CausalTaskLedger:
    """SQLite-backed task provenance used to compute rollback closure."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS causal_tasks (
                task_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                parent_task_id TEXT NOT NULL,
                state TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                read_paths_json TEXT NOT NULL,
                write_paths_json TEXT NOT NULL,
                transaction_ids_json TEXT NOT NULL,
                worktree_id TEXT NOT NULL,
                baseline TEXT NOT NULL,
                candidate_commit TEXT NOT NULL,
                candidate_tree TEXT NOT NULL,
                review_sha256 TEXT NOT NULL,
                merge_commit TEXT NOT NULL,
                rollback_commit TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {
            str(row[1]) for row in self._db.execute("PRAGMA table_info(causal_tasks)")
        }
        if "rollback_commit" not in columns:
            self._db.execute(
                "ALTER TABLE causal_tasks ADD COLUMN rollback_commit TEXT NOT NULL DEFAULT ''"
            )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def register(self, contract: TaskContract, worktree: WorktreeReceipt) -> CausalTaskRecord:
        dependencies = set(contract.dependencies)
        for prior in self.list():
            if prior.state == CausalTaskState.ROLLED_BACK:
                continue
            if _path_sets_overlap(prior.write_paths, contract.readable_paths) or _path_sets_overlap(
                prior.write_paths, contract.writable_paths
            ):
                dependencies.add(prior.task_id)
        dependencies.discard(contract.task_id)
        record = CausalTaskRecord(
            task_id=contract.task_id,
            agent_id=f"agent:{contract.agent_type}:{contract.task_id}",
            parent_task_id=contract.parent_task_id,
            state=CausalTaskState.PREPARED,
            dependencies=tuple(sorted(dependencies)),
            read_paths=tuple(contract.readable_paths),
            write_paths=tuple(contract.writable_paths),
            transaction_ids=(),
            worktree_id=worktree.worktree_id,
            baseline=worktree.baseline,
            candidate_commit="",
            candidate_tree="",
            review_sha256="",
            merge_commit="",
            rollback_commit="",
        )
        self._put(record)
        return record

    def record_review(
        self,
        task_id: str,
        review: CandidateReviewReceipt,
        transaction_ids: Iterable[str],
    ) -> CausalTaskRecord:
        current = self.get(task_id)
        updated = CausalTaskRecord(
            **{
                **_record_values(current),
                "state": CausalTaskState.REVIEWED,
                "transaction_ids": tuple(dict.fromkeys(transaction_ids)),
                "candidate_commit": review.candidate_commit,
                "candidate_tree": review.candidate_tree,
                "review_sha256": review.review_sha256,
            }
        )
        self._put(updated)
        return updated

    def record_merge(self, task_id: str, merge_commit: str) -> CausalTaskRecord:
        current = self.get(task_id)
        updated = CausalTaskRecord(
            **{
                **_record_values(current),
                "state": CausalTaskState.MERGED,
                "merge_commit": merge_commit,
            }
        )
        self._put(updated)
        return updated

    def mark_rolled_back(self, task_ids: Iterable[str], rollback_merge: str) -> None:
        for task_id in task_ids:
            current = self.get(task_id)
            self._put(CausalTaskRecord(**{
                **_record_values(current),
                "state": CausalTaskState.ROLLED_BACK,
                "rollback_commit": rollback_merge,
            }))

    def get(self, task_id: str) -> CausalTaskRecord:
        row = self._db.execute(
            "SELECT * FROM causal_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown causal task: {task_id}")
        return _row_to_record(row)

    def list(self) -> tuple[CausalTaskRecord, ...]:
        rows = self._db.execute("SELECT * FROM causal_tasks ORDER BY rowid").fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def plan_rollback(self, root_task_id: str, *, strategy: str = "cascade") -> CausalRollbackPlan:
        if strategy not in {"cascade", "replay"}:
            raise ValueError("Rollback strategy must be cascade or replay")
        records = {record.task_id: record for record in self.list()}
        root = records.get(root_task_id)
        if root is None:
            raise KeyError(f"Unknown causal task: {root_task_id}")
        if root.state != CausalTaskState.MERGED:
            raise ValueError("Only merged tasks can enter causal rollback")

        dependents: dict[str, set[str]] = {task_id: set() for task_id in records}
        for record in records.values():
            for dependency in record.dependencies:
                if dependency in dependents and record.state == CausalTaskState.MERGED:
                    dependents[dependency].add(record.task_id)

        affected: list[str] = []
        queue = [root_task_id]
        while queue:
            task_id = queue.pop(0)
            if task_id in affected:
                continue
            affected.append(task_id)
            queue.extend(sorted(dependents.get(task_id, ())))

        ordered = _topological_subset(records, affected)
        rollback_ids = tuple(reversed(ordered))
        replay_ids = tuple(task_id for task_id in ordered if task_id != root_task_id) if strategy == "replay" else ()
        transaction_ids = tuple(
            transaction_id
            for task_id in ordered
            for transaction_id in records[task_id].transaction_ids
        )
        merge_commits = tuple(records[task_id].merge_commit for task_id in rollback_ids)
        if any(not value for value in merge_commits):
            raise ValueError("Every affected task must have a durable merge commit")
        payload = {
            "root_task_id": root_task_id,
            "strategy": strategy,
            "affected_task_ids": ordered,
            "rollback_task_ids": rollback_ids,
            "replay_task_ids": replay_ids,
            "transaction_ids": transaction_ids,
            "merge_commits": merge_commits,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return CausalRollbackPlan(
            root_task_id,
            strategy,
            ordered,
            rollback_ids,
            replay_ids,
            transaction_ids,
            merge_commits,
            digest,
        )

    def _put(self, record: CausalTaskRecord) -> None:
        self._db.execute(
            """
            INSERT INTO causal_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                agent_id=excluded.agent_id,
                parent_task_id=excluded.parent_task_id,
                state=excluded.state,
                dependencies_json=excluded.dependencies_json,
                read_paths_json=excluded.read_paths_json,
                write_paths_json=excluded.write_paths_json,
                transaction_ids_json=excluded.transaction_ids_json,
                worktree_id=excluded.worktree_id,
                baseline=excluded.baseline,
                candidate_commit=excluded.candidate_commit,
                candidate_tree=excluded.candidate_tree,
                review_sha256=excluded.review_sha256,
                merge_commit=excluded.merge_commit,
                rollback_commit=excluded.rollback_commit
            """,
            (
                record.task_id,
                record.agent_id,
                record.parent_task_id,
                record.state.value,
                _json(record.dependencies),
                _json(record.read_paths),
                _json(record.write_paths),
                _json(record.transaction_ids),
                record.worktree_id,
                record.baseline,
                record.candidate_commit,
                record.candidate_tree,
                record.review_sha256,
                record.merge_commit,
                record.rollback_commit,
            ),
        )
        self._db.commit()


class CausalWorktreeCoordinator:
    """Prepare, review, merge and causally roll back agent worktrees."""

    def __init__(self, repository: Path, worktree_root: Path, ledger: CausalTaskLedger) -> None:
        self.manager = GitWorktreeManager(repository, worktree_root)
        self.workflow = WorktreeReviewWorkflow(self.manager)
        self.ledger = ledger

    def begin_task(self, contract: TaskContract) -> WorktreeReceipt:
        worktree = self.workflow.prepare()
        self.ledger.register(contract, worktree)
        return worktree

    def review_task(
        self,
        contract: TaskContract,
        worktree: WorktreeReceipt,
        *,
        verification_argv: list[str],
        commit_message: str,
    ) -> CandidateReviewReceipt:
        review = self.workflow.review(
            worktree,
            verification_argv=verification_argv,
            commit_message=commit_message,
        )
        engine = DurableChangeEngine(worktree.path)
        transactions = engine.transactions_for_task(contract.task_id)
        self.ledger.record_review(
            contract.task_id,
            review,
            (receipt.transaction_id for receipt in transactions),
        )
        return review

    def approve_task(
        self,
        task_id: str,
        review: CandidateReviewReceipt,
        *,
        approved_review_sha256: str,
    ) -> str:
        merge_commit = self.workflow.approve_and_merge(
            review,
            approved_review_sha256=approved_review_sha256,
        )
        self.ledger.record_merge(task_id, merge_commit)
        self.manager.remove(review.worktree)
        return merge_commit

    def prepare_rollback(
        self,
        plan: CausalRollbackPlan,
        *,
        verification_argv: list[str],
    ) -> CausalRollbackCandidate:
        worktree = self.workflow.prepare()
        try:
            for commit in plan.merge_commits:
                self.manager.revert_no_commit(worktree, commit)
            if plan.strategy == "replay":
                for task_id in plan.replay_task_ids:
                    candidate = self.ledger.get(task_id).candidate_commit
                    if not candidate:
                        raise ValueError(f"Task cannot be replayed without a candidate commit: {task_id}")
                    self.manager.cherry_pick_no_commit(worktree, candidate)
            review = self.workflow.review(
                worktree,
                verification_argv=verification_argv,
                commit_message=f"rollback: causal closure for {plan.root_task_id}",
            )
            return CausalRollbackCandidate(plan, review)
        except Exception:
            self.manager.remove(worktree, discard_changes=True)
            raise

    def approve_rollback(
        self,
        candidate: CausalRollbackCandidate,
        *,
        approved_plan_sha256: str,
        approved_review_sha256: str,
    ) -> str:
        if approved_plan_sha256 != candidate.plan.plan_sha256:
            raise PermissionError("approved causal closure does not match rollback plan")
        merge_commit = self.workflow.approve_and_merge(
            candidate.review,
            approved_review_sha256=approved_review_sha256,
        )
        rolled_back = (
            (candidate.plan.root_task_id,)
            if candidate.plan.strategy == "replay"
            else candidate.plan.affected_task_ids
        )
        self.ledger.mark_rolled_back(rolled_back, merge_commit)
        self.manager.remove(candidate.review.worktree)
        return merge_commit


def _json(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _row_to_record(row: sqlite3.Row) -> CausalTaskRecord:
    return CausalTaskRecord(
        task_id=str(row["task_id"]),
        agent_id=str(row["agent_id"]),
        parent_task_id=str(row["parent_task_id"]),
        state=CausalTaskState(str(row["state"])),
        dependencies=tuple(json.loads(row["dependencies_json"])),
        read_paths=tuple(json.loads(row["read_paths_json"])),
        write_paths=tuple(json.loads(row["write_paths_json"])),
        transaction_ids=tuple(json.loads(row["transaction_ids_json"])),
        worktree_id=str(row["worktree_id"]),
        baseline=str(row["baseline"]),
        candidate_commit=str(row["candidate_commit"]),
        candidate_tree=str(row["candidate_tree"]),
        review_sha256=str(row["review_sha256"]),
        merge_commit=str(row["merge_commit"]),
        rollback_commit=str(row["rollback_commit"]),
    )


def _record_values(record: CausalTaskRecord) -> dict[str, object]:
    return {
        "task_id": record.task_id,
        "agent_id": record.agent_id,
        "parent_task_id": record.parent_task_id,
        "state": record.state,
        "dependencies": record.dependencies,
        "read_paths": record.read_paths,
        "write_paths": record.write_paths,
        "transaction_ids": record.transaction_ids,
        "worktree_id": record.worktree_id,
        "baseline": record.baseline,
        "candidate_commit": record.candidate_commit,
        "candidate_tree": record.candidate_tree,
        "review_sha256": record.review_sha256,
        "merge_commit": record.merge_commit,
        "rollback_commit": record.rollback_commit,
    }


def _path_sets_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return any(_path_specs_overlap(a, b) for a in left for b in right)


def _path_specs_overlap(left: str, right: str) -> bool:
    a = left.replace("\\", "/").strip("/")
    b = right.replace("\\", "/").strip("/")
    if not a or not b:
        return False
    if any(char in a for char in "*?[") and PurePosixPath(b).match(a):
        return True
    if any(char in b for char in "*?[") and PurePosixPath(a).match(b):
        return True
    prefix_a = a.split("*")[0].split("?")[0].rstrip("/")
    prefix_b = b.split("*")[0].split("?")[0].rstrip("/")
    return (
        a == b
        or a.startswith(b + "/")
        or b.startswith(a + "/")
        or (prefix_a and prefix_b and (prefix_a.startswith(prefix_b) or prefix_b.startswith(prefix_a)))
    )


def _topological_subset(
    records: dict[str, CausalTaskRecord],
    task_ids: Iterable[str],
) -> tuple[str, ...]:
    subset = set(task_ids)
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in ordered:
            return
        if task_id in visiting:
            raise ValueError("Causal task dependency cycle detected")
        visiting.add(task_id)
        for dependency in records[task_id].dependencies:
            if dependency in subset:
                visit(dependency)
        visiting.remove(task_id)
        ordered.append(task_id)

    for task_id in sorted(subset):
        visit(task_id)
    return tuple(ordered)
