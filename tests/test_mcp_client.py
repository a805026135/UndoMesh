import pytest

from miniclaude.mcp.client import MCPClient


class FakeTransport:
    def __init__(self):
        self.calls = []

    async def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "tools/list":
            return {"tools": [{"name": "lookup", "description": "find", "inputSchema": {
                "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]
            }}]}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "found"}]}
        return {}

    async def close(self): pass


@pytest.mark.asyncio
async def test_mcp_schema_is_lazy_and_proxy_preserves_namespace():
    transport = FakeTransport()
    client = MCPClient("docs", transport)
    assert transport.calls == []
    tools = await client.proxies()
    assert tools[0].name == "mcp__docs__lookup"
    assert tools[0]._get_required_params() == ["q"]
    result = await tools[0].execute(q="agent")
    assert result.success and result.content == "found"
    assert [call[0] for call in transport.calls] == ["tools/list", "tools/call"]
