import subprocess
import pytest

from miniclaude.execution.compression import CompressionBackend
from miniclaude.execution.worktrees import GitWorktreeManager, WorktreeReviewWorkflow
from miniclaude.observability.telemetry import Telemetry, TelemetryConfig
from miniclaude.ui.review import build_review


def test_telemetry_is_opt_in_and_redacts_sensitive_attributes():
    disabled = Telemetry()
    disabled.emit("tool", {"api_key": "secret"})
    assert disabled.records == []
    enabled = Telemetry(TelemetryConfig(enabled=True))
    with enabled.span("tool", api_key="secret", tool_name="read_file"):
        pass
    assert enabled.records[0]["attributes"] == {"api_key": "[REDACTED]", "tool_name": "read_file"}


def test_review_bundle_summarizes_diff():
    review = build_review("--- a/a.py\n+++ b/a.py\n-old\n+new\n+more\n", permission_reason="approved")
    assert review.files == ("a.py",)
    assert (review.additions, review.deletions) == (2, 1)


def test_compression_backend_round_trip_and_reports_fallback():
    backend = CompressionBackend()
    data = b"agent" * 1000
    assert backend.decompress(backend.compress(data), max_output_size=len(data)) == data
    assert backend.info.chunker in {"fastcdc", "python-gear"}


def test_real_git_worktree_produces_candidate_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    manager = GitWorktreeManager(repo, tmp_path / "worktrees")
    receipt = manager.create()
    (receipt.path / "a.txt").write_text("new\n", encoding="utf-8")
    diff = manager.diff(receipt)
    assert "-old" in diff and "+new" in diff
    manager.commit_candidate(receipt, "candidate")
    manager.remove(receipt)


def test_worktree_review_digest_gates_deterministic_merge(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    manager = GitWorktreeManager(repo, tmp_path / "worktrees")
    workflow = WorktreeReviewWorkflow(manager)
    candidate = workflow.prepare()
    (candidate.path / "a.txt").write_text("candidate\n", encoding="utf-8")
    review = workflow.review(
        candidate,
        verification_argv=["git", "diff", "--check"],
        commit_message="reviewed candidate",
    )
    with pytest.raises(PermissionError, match="envelope"):
        workflow.approve_and_merge(review, approved_review_sha256="wrong")
    merged = workflow.approve_and_merge(
        review, approved_review_sha256=review.review_sha256
    )
    assert (repo / "a.txt").read_text(encoding="utf-8") == "candidate\n"
    assert merged
    manager.remove(candidate)
