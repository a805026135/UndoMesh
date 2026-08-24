"""Minimal MCP client with lazy schemas and ToolExecutor-compatible proxies."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from undomesh.tools.base import BaseTool, ToolResult


class MCPTransport(Protocol):
    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPClient:
    def __init__(self, server_name: str, transport: MCPTransport) -> None:
        self.server_name = server_name
        self.transport = transport
        self._specs: dict[str, MCPToolSpec] | None = None

    async def initialize(self) -> None:
        await self.transport.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "UndoMesh", "version": "0.1.0"},
        })

    async def list_tools(self, *, refresh: bool = False) -> list[MCPToolSpec]:
        if self._specs is None or refresh:
            response = await self.transport.request("tools/list", {})
            tools = response.get("tools", [])
            self._specs = {
                str(item["name"]): MCPToolSpec(
                    str(item["name"]), str(item.get("description", "")),
                    dict(item.get("inputSchema", {"type": "object", "properties": {}})),
                ) for item in tools
            }
        return list(self._specs.values())

    async def proxies(self) -> list["MCPRemoteTool"]:
        return [MCPRemoteTool(self, item) for item in await self.list_tools()]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # The proxy is still executed by UndoMesh ToolExecutor, so capability,
        # permission, provenance, timeout and Hook policy remain in force.
        return await self.transport.request("tools/call", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        await self.transport.close()


class MCPRemoteTool(BaseTool):
    def __init__(self, client: MCPClient, spec: MCPToolSpec) -> None:
        self.client = client
        self.spec = spec

    @property
    def name(self) -> str:
        return f"mcp__{self.client.server_name}__{self.spec.name}"

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def parameters(self) -> dict[str, Any]:
        return dict(self.spec.input_schema.get("properties", {}))

    def _get_required_params(self) -> list[str]:
        return list(self.spec.input_schema.get("required", []))

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = await self.client.call(self.spec.name, kwargs)
        except Exception as exc:
            return self._error(f"MCP call failed: {exc}")
        content = result.get("content", result)
        if isinstance(content, list):
            text = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        else:
            text = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
        return self._success(text, metadata={"mcp_server": self.client.server_name, "remote_tool": self.spec.name})
