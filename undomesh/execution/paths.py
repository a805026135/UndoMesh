"""Canonical path resolution shared by tools and capability checks."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class PathBoundaryError(ValueError):
    """Raised when a requested path escapes every granted root."""


def canonical_path(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def is_within(path: Path, roots: Iterable[Path]) -> bool:
    candidate = path.resolve(strict=False)
    for root in roots:
        try:
            candidate.relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def resolve_within_roots(
    path: str | Path,
    *,
    base: Path,
    roots: Iterable[Path],
) -> Path:
    candidate = canonical_path(path, base=base)
    if not is_within(candidate, roots):
        raise PathBoundaryError(f"Path is outside granted roots: {candidate}")
    return candidate
