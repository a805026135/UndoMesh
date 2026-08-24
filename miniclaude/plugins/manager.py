"""Trusted plugin activation without bypassing the runtime security boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Callable

from miniclaude.mcp.client import MCPClient, MCPTransport
from miniclaude.plugins.manifest import PluginManifest, load_plugin_manifest
from miniclaude.runtime.declarative_hooks import DeclarativeHookLoader


@dataclass(frozen=True, slots=True)
class PluginActivationReceipt:
    name: str
    version: str
    manifest_digest: str
    hook_names: tuple[str, ...]
    tool_names: tuple[str, ...]
    skill_paths: tuple[str, ...]
    capabilities_approved: bool


TransportFactory = Callable[[str, str], MCPTransport]


class PluginManager:
    """Activates a whole plugin only after its exact manifest digest is trusted."""

    def __init__(self, app: Any, trusted_manifest_digests: set[str]) -> None:
        self.app = app
        self.trusted_manifest_digests = trusted_manifest_digests
        self.clients: list[MCPClient] = []

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    async def activate(
        self,
        manifest_path: Path,
        *,
        transport_factory: TransportFactory,
        approve_capabilities: bool = False,
    ) -> PluginActivationReceipt:
        digest = self.digest(manifest_path)
        if digest not in self.trusted_manifest_digests:
            raise PermissionError(f"plugin manifest is not trusted; approve digest {digest}")
        manifest = load_plugin_manifest(manifest_path)
        root = manifest_path.parent.resolve()
        self._validate_assets(root, manifest)
        hook_names: list[str] = []
        if manifest.hook_manifest:
            hook_path = (root / manifest.hook_manifest).resolve()
            hook_names = DeclarativeHookLoader({DeclarativeHookLoader.digest(hook_path)}).load(
                hook_path, self.app.hook_bus
            )
        tool_names: list[str] = []
        for dependency in manifest.mcp:
            transport = transport_factory(dependency.transport, dependency.endpoint)
            client = MCPClient(dependency.name, transport)
            await client.initialize()
            for tool in await client.proxies():
                self.app.tool_registry.register(tool)
                tool_names.append(tool.name)
            self.clients.append(client)
        if approve_capabilities and tool_names:
            self.app.tool_executor.approve_registered_tools(set(tool_names))
        return PluginActivationReceipt(
            manifest.name, manifest.version, digest, tuple(hook_names), tuple(tool_names),
            tuple(manifest.skills), approve_capabilities,
        )

    async def close(self) -> None:
        for client in self.clients:
            await client.close()
        self.clients.clear()

    @staticmethod
    def _validate_assets(root: Path, manifest: PluginManifest) -> None:
        for relative in (*manifest.skills, manifest.hook_manifest):
            if not relative:
                continue
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("plugin asset escapes package root") from exc
            if not path.exists():
                raise ValueError(f"plugin asset is missing: {relative}")
