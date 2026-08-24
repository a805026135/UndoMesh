"""Validated unified patches and review evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import subprocess
from miniclaude.execution.sandbox import sanitized_subprocess_env


class PatchValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PatchSet:
    content: str
    touched_paths: tuple[str, ...]
    patch_id: str

    @classmethod
    def parse(cls, content: str) -> "PatchSet":
        paths = []
        path_pattern = r"^(?:---|\+\+\+|rename from|rename to)\s+(?:[ab]/)?(.+)$"
        for match in re.finditer(path_pattern, content, re.MULTILINE):
            raw = match.group(1).strip()
            if raw == "/dev/null":
                continue
            path = PurePosixPath(raw)
            if "\\" in raw or path.is_absolute() or PureWindowsPath(raw).is_absolute() or ".." in path.parts:
                raise PatchValidationError(f"Patch path escapes workspace: {raw}")
            paths.append(path.as_posix())
        if not paths or not any(marker in content for marker in ("@@", "rename from", "GIT binary patch")):
            raise PatchValidationError("Not a supported unified, rename, or Git binary patch")
        unique = tuple(dict.fromkeys(paths))
        return cls(content, unique, hashlib.sha256(content.encode()).hexdigest())


@dataclass(frozen=True, slots=True)
class PatchEvidence:
    patch_id: str
    touched_paths: tuple[str, ...]
    check_passed: bool
    applied: bool
    verification: tuple[tuple[str, int], ...] = ()
    rolled_back: bool = False


class GitPatchWorkflow:
    """Apply only patches accepted by git's parser and workspace path policy."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def inspect(self) -> dict[str, str]:
        return {"status": self._git("status", "--short"), "diff": self._git("diff", "--no-ext-diff")}

    def apply(self, patch: PatchSet, *, verify: tuple[str, ...] = ()) -> PatchEvidence:
        for relative in patch.touched_paths:
            (self.root / relative).resolve().relative_to(self.root)
        verification_argv: list[tuple[str, list[str]]] = []
        for command in verify:
            if any(operator in command for operator in ("&&", "||", ";", "|", ">", "<")):
                raise PatchValidationError(f"Verification command contains shell operators: {command}")
            argv = shlex.split(command, posix=os.name != "nt")
            if not argv:
                raise PatchValidationError("Verification command cannot be empty")
            argv[0] = argv[0].strip('"')
            self._validate_verification(argv)
            verification_argv.append((command, argv))
        snapshots = {
            relative: (self.root / relative).read_bytes() if (self.root / relative).is_file() else None
            for relative in patch.touched_paths
        }
        checked = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", "-"],
            cwd=self.root, input=patch.content.encode("utf-8"), capture_output=True,
            env=sanitized_subprocess_env(),
        )
        if checked.returncode:
            raise PatchValidationError(checked.stderr.decode("utf-8", errors="replace").strip() or "git apply --check failed")
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", "-"],
            cwd=self.root, input=patch.content.encode("utf-8"), capture_output=True,
            env=sanitized_subprocess_env(),
        )
        if applied.returncode:
            raise PatchValidationError(applied.stderr.decode("utf-8", errors="replace").strip() or "git apply failed")
        results = []
        for command, argv in verification_argv:
            completed = subprocess.run(
                argv, cwd=self.root, shell=False, capture_output=True, text=True,
                env=sanitized_subprocess_env(),
            )
            results.append((command, completed.returncode))
        failed = any(exit_code != 0 for _, exit_code in results)
        if failed:
            self._restore(snapshots)
            return PatchEvidence(patch.patch_id, patch.touched_paths, True, False, tuple(results), True)
        return PatchEvidence(patch.patch_id, patch.touched_paths, True, True, tuple(results))

    @staticmethod
    def _validate_verification(argv: list[str]) -> None:
        executable = Path(argv[0]).name.lower()
        if executable in {"pytest", "pytest.exe"}:
            return
        if executable in {"python", "python.exe", "python3"} and len(argv) >= 3:
            if argv[1] == "-m" and argv[2] in {"pytest", "compileall"}:
                return
        if executable in {"git", "git.exe"} and len(argv) >= 2 and argv[1] in {"diff", "status"}:
            return
        raise PatchValidationError(f"Verification executable is not allowlisted: {argv[0]}")

    def _restore(self, snapshots: dict[str, bytes | None]) -> None:
        for relative, content in snapshots.items():
            path = self.root / relative
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=self.root, text=True, capture_output=True, check=True,
            env=sanitized_subprocess_env(),
        )
        return completed.stdout
