"""JSON-RPC 2.0 and small ACP-compatible method adapter."""

from __future__ import annotations

from dataclasses import asdict
import asyncio
import json
import sys
from typing import Any, Awaitable, Callable

from undomesh.server.service import AppService


class JSONRPCDispatcher:
    def __init__(self, service: AppService) -> None:
        self.service = service
        self.methods: dict[str, Callable[[dict[str, Any]], Any | Awaitable[Any]]] = {
            "job.submit": self._submit,
            "job.get": self._get,
            "job.cancel": self._cancel,
            "job.resume": self._resume,
            "restore.submit": self._restore,
            "rewind.submit": self._rewind,
            "recovery.audit": self._audit,
            "agent/session/new": self._session_new,
            "agent/prompt": self._submit,
        }

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return self._error(request_id, -32600, "Invalid Request")
        handler = self.methods.get(request["method"])
        if handler is None:
            return self._error(request_id, -32601, "Method not found")
        try:
            value = handler(dict(request.get("params") or {}))
            if hasattr(value, "__await__"):
                value = await value
            result = self._jsonable(value)
        except (KeyError, ValueError) as exc:
            return self._error(request_id, -32602, str(exc))
        except Exception as exc:
            return self._error(request_id, -32000, str(exc))
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _submit(self, params: dict[str, Any]):
        return self.service.submit_query(str(params["prompt"]))

    def _get(self, params: dict[str, Any]):
        return self.service.jobs.get(str(params["job_id"]))

    def _cancel(self, params: dict[str, Any]):
        return self.service.cancel(str(params["job_id"]))

    def _resume(self, params: dict[str, Any]):
        return self.service.resume(str(params["job_id"]))

    def _restore(self, params: dict[str, Any]):
        return self.service.submit_restore(list(params["transaction_ids"]))

    def _rewind(self, params: dict[str, Any]):
        return self.service.submit_rewind(str(params["checkpoint_id"]), str(params.get("mode", "both")))

    def _audit(self, params: dict[str, Any]):
        return self.service.submit_recovery_audit(str(params["transaction_id"]))

    def _session_new(self, _params: dict[str, Any]):
        return {"session_id": self.service.app.start_new_session()}

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            value = asdict(value)
        if isinstance(value, dict):
            return {key: (item.value if hasattr(item, "value") else item) for key, item in value.items()}
        return value

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def serve_stdio(dispatcher: JSONRPCDispatcher) -> None:
    """Serve newline-delimited JSON-RPC without mixing logs into stdout."""
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        try:
            request = json.loads(line)
            response = await dispatcher.dispatch(request)
        except Exception as exc:
            response = JSONRPCDispatcher._error(None, -32700, f"Parse error: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
