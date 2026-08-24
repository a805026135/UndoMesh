"""Signed update metadata verification and privacy-preserving crash reports."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
import os
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class UpdateVerifier:
    def __init__(self, public_key: bytes) -> None:
        self.public_key = Ed25519PublicKey.from_public_bytes(public_key)

    def verify(self, envelope: dict[str, Any], artifact: bytes) -> dict[str, Any]:
        payload = envelope["payload"]
        wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.public_key.verify(base64.b64decode(envelope["signature"]), wire)
        if hashlib.sha256(artifact).hexdigest() != payload["sha256"]:
            raise ValueError("update artifact digest mismatch")
        return dict(payload)

    def stage(self, envelope: dict[str, Any], artifact: bytes, destination: Path) -> Path:
        """Verify before atomically staging; activation is performed on next launch."""
        self.verify(envelope, artifact)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".pending")
        temporary.write_bytes(artifact)
        os.replace(temporary, destination)
        return destination


def redact_crash_report(report: dict[str, Any]) -> dict[str, Any]:
    sensitive = re.compile(r"(key|token|secret|password|prompt|content|path)", re.I)
    return {key: "[REDACTED]" if sensitive.search(key) else value for key, value in report.items()}
