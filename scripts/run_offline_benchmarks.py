from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from undomesh.evaluation.runner import BenchmarkRunner, BenchmarkTask


def main() -> None:
    suites = (
        "runtime", "security", "context", "skill", "multi-agent", "provider",
        "changeset", "durable-recovery", "transaction-retention",
        "chunked-backup",
        "large-file-recovery",
        "restore-transactions", "prompt-checkpoints",
    )
    files = (
        "tests/test_query_loop_integration.py", "tests/test_provenance_security.py",
        "tests/test_event_compaction.py", "tests/test_skill_evolution.py",
        "tests/test_contract_scheduler.py", "tests/test_model_providers.py",
        "tests/test_patch_tool_integration.py",
        "tests/test_durable_transactions.py",
        "tests/test_transaction_retention.py",
        "tests/test_backup_store.py",
        "tests/test_hot_vault_recovery.py",
        "tests/test_restore_transactions.py", "tests/test_prompt_checkpoints.py",
    )
    tasks = tuple(BenchmarkTask(name, (sys.executable, "-m", "pytest", "-q", file)) for name, file in zip(suites, files)) + (
        BenchmarkTask(
            "control-plane",
            (
                sys.executable, "-m", "pytest", "-q",
                "tests/test_lifecycle_hooks.py", "tests/test_project_instructions.py",
                "tests/test_sandbox_credentials.py",
            ),
        ),
        BenchmarkTask(
            "production-control-plane",
            (sys.executable, "-m", "pytest", "-q", "tests/test_job_queue.py", "tests/test_manifest_hooks.py"),
        ),
        BenchmarkTask(
            "ecosystem-and-worktrees",
            (sys.executable, "-m", "pytest", "-q", "tests/test_mcp_client.py", "tests/test_stage20_components.py"),
        ),
        BenchmarkTask(
            "sdk-protocol-distribution",
            (sys.executable, "-m", "pytest", "-q", "tests/test_stage21_components.py"),
        ),
        BenchmarkTask(
            "interview-vertical-closure",
            (sys.executable, "-m", "pytest", "-q", "tests/test_interview_demo.py", "tests/test_plugin_activation.py"),
        ),
    )
    runner = BenchmarkRunner(ROOT)
    report = runner.run(tasks)
    runner.write_report(report, ROOT / "benchmarks" / "offline_report.json")
    print(f"offline benchmark: {report['passed']}/{report['total']} suites passed")
    raise SystemExit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
