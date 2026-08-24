"""Replaceable subprocess containment backends with honest capability reports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
import platform
import shutil
from pathlib import Path
from typing import Protocol, Sequence


class SandboxLevel(StrEnum):
    PROCESS = "process"
    OS = "os"


@dataclass(frozen=True, slots=True)
class SandboxReport:
    level: SandboxLevel
    backend: str
    network_isolated: bool
    filesystem_isolated: bool
    warning: str


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    memory_bytes: int = 0
    cpu_seconds: int = 0
    process_count: int = 0


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    argv: tuple[str, ...]
    cwd: str
    report: SandboxReport
    pass_fds: tuple[int, ...] = ()


class SandboxBackend(Protocol):
    report: SandboxReport

    def prepare(self, argv: Sequence[str], cwd: str) -> SandboxCommand: ...
    def attach(self, pid: int) -> None: ...


class ProcessSandboxBackend:
    """Portable process-group isolation; explicitly not a security boundary."""

    report = SandboxReport(
        SandboxLevel.PROCESS,
        "subprocess-group",
        False,
        False,
        "Application controls only; no kernel-enforced isolation",
    )

    def prepare(self, argv: Sequence[str], cwd: str) -> SandboxCommand:
        return SandboxCommand(tuple(argv), cwd, self.report)

    def attach(self, pid: int) -> None:
        return None


class BubblewrapSandboxBackend:
    """Linux bubblewrap adapter with a writable project and isolated network."""

    def __init__(self, project_root: Path, *, network_enabled: bool = False,
                 limits: ResourceLimits | None = None, seccomp_fd: int = -1) -> None:
        executable = shutil.which("bwrap")
        if platform.system() != "Linux" or executable is None:
            raise RuntimeError("bubblewrap sandbox is unavailable on this host")
        self.executable = executable
        self.project_root = project_root.resolve()
        self.network_enabled = network_enabled
        self.limits = limits or ResourceLimits()
        self.seccomp_fd = seccomp_fd
        self.report = SandboxReport(
            SandboxLevel.OS,
            "bubblewrap",
            not network_enabled,
            True,
            "Project writable, host read-only, network policy explicit; seccomp "
            + ("filter attached" if seccomp_fd >= 0 else "filter not supplied"),
        )

    def prepare(self, argv: Sequence[str], cwd: str) -> SandboxCommand:
        resolved_cwd = Path(cwd).resolve()
        try:
            resolved_cwd.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("sandbox cwd is outside the project root") from exc
        wrapped = [
            self.executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--ro-bind", "/", "/",
            "--bind", str(self.project_root), str(self.project_root),
            "--chdir", str(resolved_cwd),
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
        ]
        if not self.network_enabled:
            wrapped.append("--unshare-net")
        if self.seccomp_fd >= 0:
            wrapped.extend(("--seccomp", str(self.seccomp_fd)))
        limited = list(argv)
        prlimit = shutil.which("prlimit")
        options: list[str] = []
        if self.limits.memory_bytes:
            options.append(f"--as={self.limits.memory_bytes}")
        if self.limits.cpu_seconds:
            options.append(f"--cpu={self.limits.cpu_seconds}")
        if self.limits.process_count:
            options.append(f"--nproc={self.limits.process_count}")
        if options and prlimit:
            limited = [prlimit, *options, "--", *limited]
        elif options:
            raise RuntimeError("resource limits requested but prlimit is unavailable")
        wrapped.extend(("--", *limited))
        pass_fds = (self.seccomp_fd,) if self.seccomp_fd >= 0 else ()
        return SandboxCommand(tuple(wrapped), str(self.project_root), self.report, pass_fds)

    def attach(self, pid: int) -> None:
        return None


class WindowsJobSandboxBackend(ProcessSandboxBackend):
    """Windows Job Object limits. Filesystem/network isolation remains unavailable."""

    def __init__(self, limits: ResourceLimits | None = None) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Windows Job Object sandbox is unavailable on this host")
        self.limits = limits or ResourceLimits()
        self._job_handle: int | None = None
        self.report = SandboxReport(
            SandboxLevel.OS, "windows-job-object", False, False,
            "Kernel process/memory/CPU limits enabled; filesystem and network are not isolated",
        )

    def attach(self, pid: int) -> None:
        """Attach a child to a kill-on-close Job Object using the Win32 ABI."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION prefix is stable for these fields.
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if self.limits.process_count:
            info.BasicLimitInformation.LimitFlags |= 0x8
            info.BasicLimitInformation.ActiveProcessLimit = self.limits.process_count
        if self.limits.cpu_seconds:
            info.BasicLimitInformation.LimitFlags |= 0x2
            info.BasicLimitInformation.PerJobUserTimeLimit = self.limits.cpu_seconds * 10_000_000
        if self.limits.memory_bytes:
            info.BasicLimitInformation.LimitFlags |= 0x200
            info.JobMemoryLimit = self.limits.memory_bytes
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        process = kernel32.OpenProcess(0x0200 | 0x0400, False, pid)
        if not process or not kernel32.AssignProcessToJobObject(job, process):
            if process:
                kernel32.CloseHandle(process)
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        kernel32.CloseHandle(process)
        self._job_handle = job


SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
SAFE_ENV = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG", "LC_ALL"}


def sanitized_subprocess_env(source: dict[str, str] | None = None) -> dict[str, str]:
    source = source or dict(os.environ)
    return {
        key: value
        for key, value in source.items()
        if key.upper() in SAFE_ENV and not any(marker in key.upper() for marker in SECRET_MARKERS)
    }


def detect_sandbox() -> SandboxReport:
    if platform.system() == "Linux" and shutil.which("bwrap"):
        return SandboxReport(SandboxLevel.OS, "bubblewrap-available", True, True, "OS backend detected but explicit opt-in is required")
    return SandboxReport(SandboxLevel.PROCESS, "subprocess-group", False, False, "Application controls only; no kernel-enforced isolation")


def create_sandbox_backend(
    name: str,
    project_root: Path,
    *,
    network_enabled: bool = False,
    limits: ResourceLimits | None = None,
    seccomp_fd: int = -1,
) -> SandboxBackend:
    """Create an explicit backend; ``auto`` prefers bubblewrap when available."""
    normalized = name.strip().lower()
    if normalized == "process":
        return ProcessSandboxBackend()
    if normalized == "bubblewrap":
        return BubblewrapSandboxBackend(project_root, network_enabled=network_enabled, limits=limits, seccomp_fd=seccomp_fd)
    if normalized in {"windows", "windows_job"}:
        return WindowsJobSandboxBackend(limits)
    if normalized == "auto":
        if platform.system() == "Linux" and shutil.which("bwrap"):
            return BubblewrapSandboxBackend(project_root, network_enabled=network_enabled, limits=limits, seccomp_fd=seccomp_fd)
        if platform.system() == "Windows" and limits and any((limits.memory_bytes, limits.cpu_seconds, limits.process_count)):
            return WindowsJobSandboxBackend(limits)
        return ProcessSandboxBackend()
    raise ValueError(f"Unknown sandbox backend: {name!r}")
