from pathlib import Path
import subprocess
import sys

import pytest

from miniclaude.execution.patches import GitPatchWorkflow, PatchSet, PatchValidationError


def test_patch_rejects_escape_and_malformed() -> None:
    with pytest.raises(PatchValidationError, match="escapes"):
        PatchSet.parse("--- a/x\n+++ b/../secret\n@@ -1 +1 @@\n-a\n+b\n")
    with pytest.raises(PatchValidationError):
        PatchSet.parse("hello")


def test_verification_rejects_shell_operators(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "x.txt").write_text("a\n", encoding="utf-8")
    patch = PatchSet.parse("--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n+b\n")
    with pytest.raises(PatchValidationError, match="operators"):
        GitPatchWorkflow(tmp_path).apply(patch, verify=("echo ok && echo unsafe",))


def test_failed_verification_rolls_patch_back(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "rollback.txt"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "rollback.txt"], cwd=tmp_path, check=True)
    patch = PatchSet.parse("--- a/rollback.txt\n+++ b/rollback.txt\n@@ -1 +1 @@\n-before\n+after\n")

    evidence = GitPatchWorkflow(tmp_path).apply(
        patch, verify=(f'"{sys.executable}" -m pytest missing_test.py -q',)
    )

    assert evidence.rolled_back and not evidence.applied
    assert target.read_text(encoding="utf-8") == "before\n"


def test_git_patch_check_apply_and_evidence(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "demo.txt"
    target.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.txt"], cwd=tmp_path, check=True)
    patch = PatchSet.parse("--- a/demo.txt\n+++ b/demo.txt\n@@ -1 +1 @@\n-old\n+new\n")

    evidence = GitPatchWorkflow(tmp_path).apply(patch, verify=("git diff --check",))

    assert target.read_text(encoding="utf-8") == "new\n"
    assert evidence.touched_paths == ("demo.txt",)
    assert evidence.verification[0][1] == 0
