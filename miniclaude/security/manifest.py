"""Authenticated manifests and a small rotatable local key ring."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass(frozen=True, slots=True)
class VerifiedManifest:
    key_id: str
    payload: dict[str, Any]


class LocalKeyRing:
    """Owner-only local key ring; production deployments may replace this adapter."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.index = directory / "index.json"
        if not self.index.exists():
            self.rotate()

    @property
    def active_key_id(self) -> str:
        return json.loads(self.index.read_text(encoding="utf-8"))["active_key_id"]

    def get(self, key_id: str) -> bytes:
        path = self.directory / f"{key_id}.key"
        if not path.is_file():
            raise KeyError(f"manifest key unavailable: {key_id}")
        return base64.b64decode(path.read_text(encoding="ascii"))

    def rotate(self) -> str:
        key_id = f"key_{uuid4().hex}"
        key_path = self.directory / f"{key_id}.key"
        key_path.write_text(base64.b64encode(os.urandom(32)).decode(), encoding="ascii")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        previous = []
        if self.index.exists():
            old = json.loads(self.index.read_text(encoding="utf-8"))
            previous = [old["active_key_id"], *old.get("previous_key_ids", [])]
        self.index.write_text(json.dumps({"active_key_id": key_id, "previous_key_ids": previous}), encoding="utf-8")
        return key_id

    def audit(self, required_key_ids: set[str]) -> dict[str, list[str]]:
        available = {path.stem for path in self.directory.glob("key_*.key")}
        return {
            "available": sorted(required_key_ids & available),
            "missing": sorted(required_key_ids - available),
            "unreferenced": sorted(available - required_key_ids),
        }

    def export(self, key_id: str) -> str:
        """Explicit escrow/export material; callers must obtain human approval."""
        return base64.b64encode(self.get(key_id)).decode("ascii")


class ManifestAuthenticator:
    SCHEMA_VERSION = 1

    def __init__(self, keys: LocalKeyRing) -> None:
        self.keys = keys

    def seal(self, payload: dict[str, Any]) -> dict[str, Any]:
        key_id = self.keys.active_key_id
        body = {"schema_version": self.SCHEMA_VERSION, "key_id": key_id, "payload": payload}
        signature = hmac.new(self.keys.get(key_id), _canonical(body), hashlib.sha256).hexdigest()
        return {**body, "algorithm": "HMAC-SHA256", "signature": signature}

    def verify(self, envelope: dict[str, Any]) -> VerifiedManifest:
        if envelope.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema")
        if envelope.get("algorithm") != "HMAC-SHA256":
            raise ValueError("unsupported manifest authentication algorithm")
        body = {key: envelope[key] for key in ("schema_version", "key_id", "payload")}
        expected = hmac.new(self.keys.get(str(envelope["key_id"])), _canonical(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(envelope.get("signature", ""))):
            raise ValueError("manifest authentication failed")
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise ValueError("manifest payload must be an object")
        return VerifiedManifest(str(envelope["key_id"]), payload)
