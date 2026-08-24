from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import time


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    task_id: str
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskRun:
    task_id: str
    success: bool
    exit_code: int
    duration_ms: float


class BenchmarkRunner:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def run(self, tasks: tuple[BenchmarkTask, ...]) -> dict:
        runs = []
        for task in tasks:
            started = time.perf_counter()
            completed = subprocess.run(task.command, cwd=self.root, capture_output=True, text=True)
            runs.append(TaskRun(task.task_id, completed.returncode == 0, completed.returncode, round((time.perf_counter() - started) * 1000, 2)))
        return {
            "schema_version": 1,
            "tasks": [asdict(run) for run in runs],
            "total": len(runs),
            "passed": sum(run.success for run in runs),
            "failed": sum(not run.success for run in runs),
        }

    @staticmethod
    def write_report(report: dict, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
