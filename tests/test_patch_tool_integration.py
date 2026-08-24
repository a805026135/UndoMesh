from pathlib import Path
import subprocess

import pytest

from undomesh.core.tool_executor import ToolExecutor
from undomesh.execution.capabilities import CapabilitySet, ToolExecutionContext
from undomesh.security.permission import PermissionManager
from undomesh.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_patch_tool_is_registered_and_applies_transactionally(config, tmp_project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_project, check=True)
    subprocess.run(["git", "add", "hello.py"], cwd=tmp_project, check=True)
    registry = ToolRegistry(config)
    registry.register_all()
    executor = ToolExecutor(registry, config, permission_manager=PermissionManager(config))
    patch = '--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-print("Hello, World!")\n+print("Patched")\n'

    result = await executor.execute_single(
        "apply_patch_set",
        {"patch": patch, "verification_commands": ["git diff --check"]},
        ToolExecutionContext(
            capability_set=CapabilitySet.for_project(tmp_project, set(registry.get_names()), shell_enabled=True)
        ),
    )

    assert result.success
    assert 'print("Patched")' in (tmp_project / "hello.py").read_text(encoding="utf-8")
    assert result.metadata["patch_id"]


@pytest.mark.asyncio
async def test_patch_tool_denies_sensitive_target(config, tmp_project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_project, check=True)
    registry = ToolRegistry(config)
    registry.register_all()
    executor = ToolExecutor(registry, config, permission_manager=PermissionManager(config))
    patch = "--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+SECRET=value\n"

    result = await executor.execute_single("apply_patch_set", {"patch": patch})

    assert not result.success
    assert result.metadata.get("denied")
    assert not (tmp_project / ".env").exists()


@pytest.mark.asyncio
async def test_rollback_tool_restores_committed_change(config, tmp_project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_project, check=True)
    subprocess.run(["git", "add", "hello.py"], cwd=tmp_project, check=True)
    registry = ToolRegistry(config)
    registry.register_all()
    executor = ToolExecutor(registry, config, permission_manager=PermissionManager(config))
    patch = '--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-print("Hello, World!")\n+print("Temporary")\n'
    applied = await executor.execute_single("apply_patch_set", {"patch": patch})
    assert applied.success

    rolled_back = await executor.execute_single(
        "rollback_change_set", {"transaction_id": applied.metadata["transaction_id"]}
    )

    assert rolled_back.success
    assert 'Hello, World!' in (tmp_project / "hello.py").read_text(encoding="utf-8")

    status = await executor.execute_single(
        "change_transaction_status", {"transaction_id": applied.metadata["transaction_id"]}
    )
    assert status.success
    assert '"state": "committed"' in status.content
    assert rolled_back.metadata["state"] == "completed"
    assert rolled_back.metadata["restore_id"]
    assert '"transitions"' in status.content
