import json

import pytest

from undomesh.plugins.manager import PluginManager
from undomesh.runtime.hooks import HookBus
from undomesh.tools.registry import ToolRegistry


class FakeTransport:
    async def request(self, method, params=None):
        if method == "tools/list":
            return {"tools": [{"name": "search", "description": "search docs", "inputSchema": {"type": "object", "properties": {}}}]}
        return {}
    async def close(self): pass


@pytest.mark.asyncio
async def test_trusted_plugin_activates_hook_and_mcp_through_capabilities(config, tmp_path):
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"schema_version": 1, "hooks": [{
        "name": "guard", "point": "pre_tool", "matcher": "mcp__docs__*", "action": "ask"
    }]}), encoding="utf-8")
    (tmp_path / "skill").mkdir()
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({
        "name": "docs-plugin", "version": "1.0.0", "skills": ["skill"],
        "hook_manifest": "hooks.json", "mcp": [{"name": "docs", "transport": "stdio", "endpoint": "fake"}]
    }), encoding="utf-8")
    registry = ToolRegistry(config)
    executor = type("Executor", (), {
        "approve_registered_tools": lambda self, names: setattr(self, "approved", names)
    })()
    app = type("App", (), {"hook_bus": HookBus(), "tool_registry": registry, "tool_executor": executor})()
    manager = PluginManager(app, {PluginManager.digest(manifest)})
    receipt = await manager.activate(
        manifest, transport_factory=lambda transport, endpoint: FakeTransport(), approve_capabilities=True
    )
    assert receipt.tool_names == ("mcp__docs__search",)
    assert receipt.hook_names == ("guard",)
    assert executor.approved == {"mcp__docs__search"}
    await manager.close()


@pytest.mark.asyncio
async def test_plugin_activation_requires_exact_manifest_digest(config, tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text('{"name":"docs-plugin","version":"1"}', encoding="utf-8")
    app = type("App", (), {})()
    with pytest.raises(PermissionError, match="approve digest"):
        await PluginManager(app, set()).activate(manifest, transport_factory=lambda *_: FakeTransport())
