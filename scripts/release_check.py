from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "docs/ROADMAP.md", "docs/THREAT_MODEL.md",
    "docs/EVENT_PROTOCOL.md", "docs/PROVIDER_HARNESS.md",
    "docs/DURABLE_CHANGE_TRANSACTIONS.md", "docs/TRANSACTION_RETENTION.md",
    "docs/CHUNKED_BACKUP_STORE.md", "docs/CLAUDE_REWIND_COMPARISON.md",
    "docs/LARGE_FILE_RECOVERY_VAULT.md",
    "docs/RESTORE_TRANSACTIONS.md", "docs/PROMPT_CHECKPOINTS.md",
    "docs/DESKTOP_CLIENT.md", "docs/SYSTEM_AUDIT.md",
    "docs/COMPETITIVE_ANALYSIS.md", "docs/RESUME_PROJECT.md",
    "docs/PROJECT_INTRODUCTION.md", "docs/INTERVIEW_QA.md",
    "docs/PROJECT_INSTRUCTIONS_AND_HOOKS.md",
    "docs/STAGES_19_21_IMPLEMENTATION.md",
    "docs/INTERVIEW_DEMO.md",
    "benchmarks/tasks.json", "benchmarks/stage9_qwen37_flash_report.json",
)


def main() -> None:
    missing = [item for item in REQUIRED if not (ROOT / item).exists()]
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT).returncode
    compile_result = subprocess.run([sys.executable, "-m", "compileall", "-q", "undomesh"], cwd=ROOT).returncode
    report = {"missing": missing, "tests_exit_code": tests, "compile_exit_code": compile_result, "ready": not missing and tests == 0 and compile_result == 0}
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
