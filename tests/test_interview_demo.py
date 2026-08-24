import json
import subprocess
import sys
from pathlib import Path


def test_interview_demo_proves_three_vertical_loops():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/demo_interview.py"], cwd=root,
        text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["restore"]["job_state"] == "succeeded"
    assert report["restore"]["content"] == "zero"
    assert report["conflict"]["preflight"] == "conflicted"
    assert report["conflict"]["user_content_preserved"] == "newer-user-content"
    assert report["worktree"]["verified"]
    assert report["worktree"]["content"] == "reviewed-candidate"
