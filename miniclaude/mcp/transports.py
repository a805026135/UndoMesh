"""STDIO and Streamable-HTTP JSON-RPC transports for MCP."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.request import Request, urlopen


class StdioTransport:
    def __init__(self, argv: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None) -> None:
        self.argv, self.cwd, self.env = argv, cwd, env
        self.process: asyncio.subprocess.Process | None = None
        self._id = 0
        self._lock = asyncio.Lock()

    async def _start(self) -> None:
        if self.process is None:
            self.process = await asyncio.create_subprocess_exec(
                *self.argv, cwd=self.cwd, env=self.env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._start()
        assert self.process and self.process.stdin and self.process.stdout
        async with self._lock:
            self._id += 1
            request_id = self._id
            wire = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}).encode() + b"\n"
            self.process.stdin.write(wire)
            await self.process.stdin.drain()
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise ConnectionError("MCP STDIO server closed")
                message = json.loads(line)
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(str(message["error"]))
                    return dict(message.get("result", {}))

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


class StreamableHTTPTransport:
    def __init__(self, url: str, *, oauth_token: str = "") -> None:
        self.url = url
        self._token = oauth_token
        self._id = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        def send() -> dict[str, Any]:
            with urlopen(Request(self.url, data=payload, headers=headers, method="POST"), timeout=30) as response:
                body = response.read()
            message = json.loads(body)
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return dict(message.get("result", {}))

        return await asyncio.to_thread(send)

    async def close(self) -> None:
        return None
