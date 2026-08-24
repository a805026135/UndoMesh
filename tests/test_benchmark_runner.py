from pathlib import Path
import sys

from undomesh.evaluation.runner import BenchmarkRunner, BenchmarkTask


def test_benchmark_runner_records_pass_and_failure(tmp_path: Path) -> None:
    runner = BenchmarkRunner(tmp_path)
    report = runner.run((
        BenchmarkTask("pass", (sys.executable, "-c", "raise SystemExit(0)")),
        BenchmarkTask("fail", (sys.executable, "-c", "raise SystemExit(3)")),
    ))
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert all(run["duration_ms"] >= 0 for run in report["tasks"])
