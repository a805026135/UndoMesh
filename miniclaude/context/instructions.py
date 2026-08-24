"""Layered project instructions with deterministic discovery and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstructionSource:
    path: Path
    scope: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class InstructionBundle:
    content: str
    sources: tuple[InstructionSource, ...]
    byte_count: int
    truncated: bool = False

    def describe(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "truncated": self.truncated,
            "sources": [
                {
                    "path": str(source.path),
                    "scope": source.scope,
                    "byte_count": source.byte_count,
                }
                for source in self.sources
            ],
        }


class ProjectInstructionLoader:
    """Load user and project instructions from broad to specific scope.

    At each directory an override replaces the normal file for that directory;
    deeper directories appear later and therefore have higher precedence. The
    loader only reads files and never executes their contents.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        working_directory: Path | None = None,
        user_config_dir: Path | None = None,
        max_bytes: int = 32 * 1024,
        fallback_names: tuple[str, ...] = (),
    ) -> None:
        self.project_root = project_root.resolve()
        candidate = (working_directory or self.project_root).resolve()
        try:
            candidate.relative_to(self.project_root)
        except ValueError:
            candidate = self.project_root
        self.working_directory = candidate
        self.user_config_dir = user_config_dir
        self.max_bytes = max(0, max_bytes)
        self.fallback_names = tuple(
            name for name in fallback_names
            if name and Path(name).name == name
        )

    def load(self) -> InstructionBundle:
        candidates: list[tuple[Path, str]] = []
        if self.user_config_dir is not None:
            selected = self._select(self.user_config_dir)
            if selected is not None:
                candidates.append((selected, "user"))

        relative = self.working_directory.relative_to(self.project_root)
        directories = [self.project_root]
        cursor = self.project_root
        for part in relative.parts:
            cursor /= part
            directories.append(cursor)
        for index, directory in enumerate(directories):
            selected = self._select(directory)
            if selected is not None:
                scope = "project" if index == 0 else "directory"
                candidates.append((selected, scope))

        sections: list[str] = []
        sources: list[InstructionSource] = []
        used = 0
        truncated = False
        for path, scope in candidates:
            raw = path.read_bytes()
            if not raw.strip():
                continue
            label = self._display_label(path, scope)
            header = f"## Instructions from {label}\n".encode()
            required = len(header) + len(raw) + 2
            if used + required > self.max_bytes:
                truncated = True
                break
            text = raw.decode("utf-8", errors="replace").strip()
            sections.append(f"## Instructions from {label}\n{text}")
            sources.append(InstructionSource(path, scope, len(raw)))
            used += required

        return InstructionBundle(
            content="\n\n".join(sections),
            sources=tuple(sources),
            byte_count=used,
            truncated=truncated,
        )

    def _select(self, directory: Path) -> Path | None:
        for name in ("AGENTS.override.md", "AGENTS.md", *self.fallback_names):
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
        return None

    def _display_label(self, path: Path, scope: str) -> str:
        """Return a stable prompt label without leaking an absolute host path."""
        if scope == "user":
            return f"user/{path.name}"
        try:
            relative = path.relative_to(self.project_root)
        except ValueError:
            return f"project/{path.name}"
        return f"project/{relative.as_posix()}"
