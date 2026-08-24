"""Key-free end-to-end interview demo: restore, conflict refusal and reviewed worktree."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from miniclaude.execution.patches import PatchSet
from miniclaude.execution.transactions import DurableChangeEngine
from miniclaude.execution.worktrees import GitWorktreeManager, WorktreeReviewWorkflow
from miniclaude.observability.telemetry import Telemetry, TelemetryConfig
from miniclaude.runtime.jobs import JobState
from miniclaude.server.service import AppService


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "interview@example.invalid")
    git(path, "config", "user.name", "Interview Demo")


def change(engine: DurableChangeEngine, before: str, after: str):
    target = engine.root / "state.txt"
    if not target.exists():
        target.write_text(before + "\n", encoding="utf-8")
    return engine.apply(PatchSet.parse(
        f"--- a/state.txt\n+++ b/state.txt\n@@ -1 +1 @@\n-{before}\n+{after}\n"
    ))


async def wait_job(service: AppService, job_id: str):
    for _ in range(500):
        job = service.jobs.get(job_id)
        if job.state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}:
            return job
        await asyncio.sleep(0.01)
    raise TimeoutError(job_id)


async def main_async() -> dict[str, object]:
    with TemporaryDirectory(prefix="miniclaude-interview-") as temporary:
        base = Path(temporary)
        repo = base / "recovery"
        init_repo(repo)
        engine = DurableChangeEngine(repo)
        first = change(engine, "zero", "one")
        second = change(engine, "one", "two")
        preview = engine.preview_restore((first.transaction_id, second.transaction_id))

        class DemoApp:
            change_engine = engine
            checkpoint_manager = None
            async def initialize(self): pass
            async def shutdown(self): pass

        telemetry = Telemetry(TelemetryConfig(enabled=True))
        service = AppService(DemoApp(), base / "jobs.db", telemetry=telemetry)
        await service.start()
        restore_job = service.submit_restore([first.transaction_id, second.transaction_id])
        completed = await wait_job(service, restore_job.job_id)
        restored_content = (repo / "state.txt").read_text(encoding="utf-8").strip()

        conflict_source = change(engine, "zero", "agent")
        (repo / "state.txt").write_text("newer-user-content\n", encoding="utf-8")
        conflict = engine.preview_restore((conflict_source.transaction_id,))
        await service.close()

        work_repo = base / "worktree"
        init_repo(work_repo)
        (work_repo / "feature.txt").write_text("base\n", encoding="utf-8")
        git(work_repo, "add", "feature.txt")
        git(work_repo, "commit", "-qm", "base")
        manager = GitWorktreeManager(work_repo, base / "candidates")
        workflow = WorktreeReviewWorkflow(manager)
        candidate = workflow.prepare()
        (candidate.path / "feature.txt").write_text("reviewed-candidate\n", encoding="utf-8")
        review = workflow.review(
            candidate, verification_argv=["git", "diff", "--check"],
            commit_message="candidate",
        )
        merge_commit = workflow.approve_and_merge(
            review, approved_diff_sha256=review.diff_sha256
        )
        merged_content = (work_repo / "feature.txt").read_text(encoding="utf-8").strip()
        manager.remove(candidate)

        return {
            "restore": {
                "preflight": preview.health,
                "job_state": completed.state.value,
                "restore_state": (completed.result or {}).get("state"),
                "content": restored_content,
                "telemetry_span": telemetry.records[0]["name"],
            },
            "conflict": {
                "preflight": conflict.health,
                "paths": list(conflict.conflicts),
                "user_content_preserved": (repo / "state.txt").read_text(encoding="utf-8").strip(),
            },
            "worktree": {
                "verified": review.verification_exit_code == 0,
                "diff_sha256": review.diff_sha256,
                "merged_commit": merge_commit,
                "content": merged_content,
            },
        }


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
