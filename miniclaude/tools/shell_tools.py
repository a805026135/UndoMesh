"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
import shlex
from typing import Any

from miniclaude.core.config import MiniClaudeConfig
from miniclaude.execution.paths import PathBoundaryError, resolve_within_roots
from miniclaude.execution.sandbox import ResourceLimits, create_sandbox_backend, sanitized_subprocess_env
from miniclaude.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Commands that are always blocked
BLOCKED_COMMANDS = frozenset({
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "fork bomb",
})

# Patterns that require confirmation
DANGEROUS_PATTERNS = [
    "rm -rf",
    "rm -r",
    "DROP TABLE",
    "DROP DATABASE",
    "DELETE FROM",
    "sudo rm",
    "chmod 777",
    "> /dev/",
    "shutdown",
    "reboot",
    "format ",
    "git push --force",
    "git reset --hard",
]


class ShellTool(BaseTool):
    """Execute shell commands in a controlled environment."""

    def __init__(self, config: MiniClaudeConfig) -> None:
        self._config = config
        self._sandbox = create_sandbox_backend(
            config.sandbox_backend,
            config.effective_project_dir(),
            network_enabled=config.sandbox_network_enabled,
            limits=ResourceLimits(
                memory_bytes=config.sandbox_memory_bytes,
                cpu_seconds=config.sandbox_cpu_seconds,
                process_count=config.sandbox_process_count,
            ),
            seccomp_fd=config.sandbox_seccomp_fd,
        )

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output (stdout + stderr). "
            "Use for running tests, building projects, checking git status, etc. "
            "Commands are executed in the project directory. "
            "Timeout defaults to 120 seconds."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "command": {
                "type": "string",
                "description": "Shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default: 120",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for command. Default: project directory",
            },
        }

    def _get_required_params(self) -> list[str]:
        return ["command"]

    async def execute(self, **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 120) or 120
        cwd = kwargs.get("cwd", "")

        if not command.strip():
            return self._error("Empty command")

        # Safety check: blocked commands
        cmd_lower = command.lower().strip()
        for blocked in BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return self._error(f"Command blocked for safety: contains '{blocked}'")

        # Safety check: dangerous patterns
        is_dangerous = any(p.lower() in cmd_lower for p in DANGEROUS_PATTERNS)
        if is_dangerous and self._config.confirm_dangerous:
            logger.warning("Dangerous command detected: %s", command)
            # In the actual flow, the permission manager handles confirmation
            # Here we just mark it
            return self._error(
                f"⚠️  Dangerous command detected and requires confirmation: {command}\n"
                f"Use the permission system to approve this command."
            )

        try:
            project_root = self._config.effective_project_dir()
            work_dir = str(
                resolve_within_roots(cwd or ".", base=project_root, roots=(project_root,))
            )
        except PathBoundaryError as exc:
            return self._error(str(exc))

        logger.debug("Executing: %s (cwd=%s, timeout=%ds)", command, work_dir, timeout)

        try:
            # Determine shell based on platform
            if platform.system() == "Windows":
                shell_cmd = ["cmd", "/c", command]
            else:
                shell_cmd = ["bash", "-c", command]
            sandbox_command = self._sandbox.prepare(shell_cmd, work_dir)

            process_kwargs: dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.STDOUT,
                "cwd": sandbox_command.cwd,
                "env": sanitized_subprocess_env(),
            }
            if platform.system() == "Windows":
                process_kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
            else:
                process_kwargs["start_new_session"] = True
                if sandbox_command.pass_fds:
                    process_kwargs["pass_fds"] = sandbox_command.pass_fds
            process = await asyncio.create_subprocess_exec(*sandbox_command.argv, **process_kwargs)
            self._sandbox.attach(process.pid)

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await _terminate_process_tree(process)
                return self._error(
                    f"Command timed out after {timeout}s: {command}"
                )
            except asyncio.CancelledError:
                await _terminate_process_tree(process)
                raise

            output = stdout.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            # Truncate very long output
            max_output = 50000
            truncated = False
            if len(output) > max_output:
                output = output[:max_output] + f"\n... (output truncated, {len(stdout)} bytes total)"
                truncated = True

            if exit_code == 0:
                content = output if output else "(command completed successfully with no output)"
            else:
                content = f"(exit code: {exit_code})\n{output}" if output else f"(exit code: {exit_code}, no output)"

            logger.debug("Command done: exit=%d, output=%d chars", exit_code, len(content))
            return self._success(
                content,
                truncated=truncated,
                metadata={
                    "exit_code": exit_code,
                    "command": command,
                    "sandbox": sandbox_command.report.level.value,
                    "sandbox_backend": sandbox_command.report.backend,
                    "network_isolated": sandbox_command.report.network_isolated,
                },
            )

        except FileNotFoundError:
            return self._error(f"Command not found: {command.split()[0]}")
        except PermissionError:
            return self._error(f"Permission denied: {command}")
        except Exception as e:
            return self._error(f"Command execution failed: {e}")


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Best-effort termination of the shell and children on timeout/cancel."""
    if process.returncode is not None:
        return
    try:
        if platform.system() == "Windows":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, FileNotFoundError, PermissionError):
        process.kill()
    finally:
        await process.wait()
