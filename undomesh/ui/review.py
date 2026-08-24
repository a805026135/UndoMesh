"""Review-center projections for CLI and desktop surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    files: tuple[str, ...]
    additions: int
    deletions: int
    diff: str
    permission_reason: str
    receipt: dict[str, Any]


def build_review(diff: str, *, permission_reason: str = "", receipt: dict[str, Any] | None = None) -> ReviewBundle:
    files: list[str] = []
    additions = deletions = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return ReviewBundle(tuple(dict.fromkeys(files)), additions, deletions, diff, permission_reason, receipt or {})
