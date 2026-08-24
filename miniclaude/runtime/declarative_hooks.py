"""Non-executable declarative hooks gated by an explicit trust digest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from miniclaude.runtime.hooks import HookBus, HookOutcome, HookPoint, HookVerdict


class DeclarativeHookLoader:
    """Loads only built-in policy actions; repository manifests never execute code."""

    def __init__(self, trusted_digests: set[str]) -> None:
        self.trusted_digests = trusted_digests

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def load(self, path: Path, bus: HookBus) -> list[str]:
        digest = self.digest(path)
        if digest not in self.trusted_digests:
            raise PermissionError(f"hook manifest is not trusted; approve digest {digest}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != 1 or not isinstance(document.get("hooks"), list):
            raise ValueError("invalid hook manifest")
        names: list[str] = []
        for raw in document["hooks"]:
            self._register(raw, bus)
            names.append(raw["name"])
        return names

    @staticmethod
    def _register(raw: dict[str, Any], bus: HookBus) -> None:
        allowed = {"name", "point", "matcher", "priority", "action", "reason"}
        if set(raw) - allowed:
            raise ValueError("declarative hooks contain unsupported executable fields")
        point = HookPoint(raw["point"])
        if point != HookPoint.PRE_TOOL:
            raise ValueError("declarative hooks currently support pre_tool only")
        verdict = HookVerdict(raw["action"])
        if verdict not in {HookVerdict.DENY, HookVerdict.ASK, HookVerdict.ALLOW}:
            raise ValueError("unsupported declarative hook action")
        reason = str(raw.get("reason", "declarative policy"))
        bus.register(
            lambda _context, verdict=verdict, reason=reason: HookOutcome(verdict, reason),
            point=point,
            matcher=str(raw.get("matcher", "*")),
            name=str(raw["name"]),
            priority=int(raw.get("priority", 0)),
        )
