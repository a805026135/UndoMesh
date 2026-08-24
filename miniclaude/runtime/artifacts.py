"""Content-addressed storage for large runtime artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ArtifactIntegrityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    path: Path
    size: int
    sensitive: bool = False


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(
        self,
        content: str,
        *,
        sensitive: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        data = content.encode("utf-8")
        artifact_id = hashlib.sha256(data).hexdigest()
        data_path = self.root / f"{artifact_id}.txt"
        meta_path = self.root / f"{artifact_id}.json"
        if not data_path.exists():
            data_path.write_bytes(data)
        meta_path.write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "size": len(data),
                    "sensitive": sensitive,
                    "metadata": metadata or {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return ArtifactRef(artifact_id, data_path, len(data), sensitive)

    def read_text(self, artifact_id: str) -> str:
        path = self.root / f"{artifact_id}.txt"
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != artifact_id:
            raise ArtifactIntegrityError(f"Artifact hash mismatch: {artifact_id}")
        return data.decode("utf-8")
