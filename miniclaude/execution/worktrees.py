"""Real Git worktree lifecycle with deterministic patch handoff."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class WorktreeReceipt:
    worktree_id: str
    path: Path
    branch: str
    baseline: str


@dataclass(frozen=True, slots=True)
class CandidateReviewReceipt:
    worktree: WorktreeReceipt
    diff_sha256: str
    changed_paths: tuple[str, ...]
    verification_argv: tuple[str, ...]
    verification_exit_code: int
    candidate_commit: str


class GitWorktreeManager:
    def __init__(self, repository: Path, root: Path) -> None:
        self.repository = repository.resolve()
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, *, baseline: str = "HEAD") -> WorktreeReceipt:
        commit = self._git("rev-parse", "--verify", baseline).strip()
        worktree_id = f"wt_{uuid4().hex}"
        branch = f"miniclaude/{worktree_id}"
        path = self.root / worktree_id
        self._git("worktree", "add", "-b", branch, str(path), commit)
        return WorktreeReceipt(worktree_id, path, branch, commit)

    def diff(self, receipt: WorktreeReceipt) -> str:
        return self._git("-C", str(receipt.path), "diff", "--binary", receipt.baseline)

    def verify(self, receipt: WorktreeReceipt, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if not argv:
            raise ValueError("verification argv is empty")
        return subprocess.run(argv, cwd=receipt.path, text=True, capture_output=True, check=False)

    def commit_candidate(self, receipt: WorktreeReceipt, message: str) -> str:
        self._git("-C", str(receipt.path), "add", "-A")
        self._git("-C", str(receipt.path), "commit", "-m", message)
        return self._git("-C", str(receipt.path), "rev-parse", "HEAD").strip()

    def merge(self, receipt: WorktreeReceipt) -> str:
        if self._git("status", "--porcelain").strip():
            raise RuntimeError("parent worktree is dirty; deterministic merge refused")
        self._git("merge", "--no-ff", "--no-edit", receipt.branch)
        return self._git("rev-parse", "HEAD").strip()

    def remove(self, receipt: WorktreeReceipt, *, discard_changes: bool = False) -> None:
        args = ["worktree", "remove"]
        if discard_changes:
            args.append("--force")
        self._git(*args, str(receipt.path))

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.repository), *args], text=True, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout


class WorktreeReviewWorkflow:
    """Candidate → verification → immutable review digest → approved merge."""

    def __init__(self, manager: GitWorktreeManager) -> None:
        self.manager = manager

    def prepare(self, *, baseline: str = "HEAD") -> WorktreeReceipt:
        return self.manager.create(baseline=baseline)

    def review(
        self,
        worktree: WorktreeReceipt,
        *,
        verification_argv: list[str],
        commit_message: str,
    ) -> CandidateReviewReceipt:
        verification = self.manager.verify(worktree, verification_argv)
        if verification.returncode:
            raise RuntimeError(
                f"candidate verification failed ({verification.returncode}): "
                f"{verification.stdout}\n{verification.stderr}"
            )
        candidate_commit = self.manager.commit_candidate(worktree, commit_message)
        diff = self.manager.diff(worktree)
        paths = tuple(
            line[6:] for line in diff.splitlines() if line.startswith("+++ b/")
        )
        return CandidateReviewReceipt(
            worktree, hashlib.sha256(diff.encode()).hexdigest(),
            tuple(dict.fromkeys(paths)), tuple(verification_argv),
            verification.returncode, candidate_commit,
        )

    def approve_and_merge(self, review: CandidateReviewReceipt, *, approved_diff_sha256: str) -> str:
        if approved_diff_sha256 != review.diff_sha256:
            raise PermissionError("approved review digest does not match candidate")
        current = self.manager.diff(review.worktree)
        if hashlib.sha256(current.encode()).hexdigest() != review.diff_sha256:
            raise RuntimeError("candidate changed after review")
        return self.manager.merge(review.worktree)
