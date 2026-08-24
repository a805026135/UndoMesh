from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random

import pytest

from undomesh.execution.backup_store import (
    BackupStoreError,
    ChunkingPolicy,
    EncryptedChunkStore,
)


def _manifest(store: EncryptedChunkStore, digest: str) -> dict:
    return json.loads((store.manifests / f"{digest}.json").read_text(encoding="utf-8"))


def test_encrypted_compressed_chunk_round_trip_hides_plaintext(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path, encryption=True)
    content = (b"highly-secret-source-line\n" * 10_000)

    digest = store.store(content, max_bytes=2 * 1024 * 1024)

    assert store.load(digest) == content
    encoded_chunks = b"".join(path.read_bytes() for path in store.chunks.iterdir())
    assert b"highly-secret-source-line" not in encoded_chunks
    assert store.storage_bytes() < len(content)


def test_content_defined_chunking_reuses_chunks_after_insertion(tmp_path: Path) -> None:
    policy = ChunkingPolicy(minimum=1024, average=4096, maximum=16384)
    store = EncryptedChunkStore(tmp_path, encryption=True, chunking=policy)
    base = random.Random(7).randbytes(512 * 1024)
    modified = base[:200_000] + b"inserted-region" * 20 + base[200_000:]

    original_digest = store.store(base, max_bytes=4 * 1024 * 1024)
    modified_digest = store.store(modified, max_bytes=4 * 1024 * 1024)
    original_chunks = {item["digest"] for item in _manifest(store, original_digest)["chunks"]}
    modified_chunks = {item["digest"] for item in _manifest(store, modified_digest)["chunks"]}

    assert len(original_chunks & modified_chunks) >= int(len(original_chunks) * 0.8)
    assert store.load(modified_digest) == modified


def test_identical_content_creates_one_manifest_and_one_chunk_set(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    content = b"same-content" * 20_000
    first = store.store(content, max_bytes=2 * 1024 * 1024)
    chunks_before = {path.name for path in store.chunks.iterdir()}

    second = store.store(content, max_bytes=2 * 1024 * 1024)

    assert first == second
    assert len(list(store.manifests.iterdir())) == 1
    assert {path.name for path in store.chunks.iterdir()} == chunks_before


def test_authenticated_encryption_detects_chunk_tampering(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    digest = store.store(b"tamper target" * 10_000, max_bytes=1024 * 1024)
    chunk = next(store.chunks.iterdir())
    encoded = bytearray(chunk.read_bytes())
    encoded[-1] ^= 1
    chunk.write_bytes(encoded)

    with pytest.raises(BackupStoreError, match="authentication failed"):
        store.load(digest)


def test_wrong_external_key_cannot_decrypt_backup(tmp_path: Path) -> None:
    first = EncryptedChunkStore(tmp_path, encryption=True, encryption_key=b"a" * 32)
    digest = first.store(b"encrypted", max_bytes=1024 * 1024)
    second = EncryptedChunkStore(tmp_path, encryption=True, encryption_key=b"b" * 32)

    with pytest.raises(BackupStoreError, match="unavailable"):
        second.load(digest)


def test_legacy_raw_blob_remains_readable(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    content = b"legacy-backup"
    digest = hashlib.sha256(content).hexdigest()
    (store.legacy_blobs / digest).write_bytes(content)

    assert store.load(digest) == content


def test_empty_file_uses_manifest_and_round_trips(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    digest = store.store(b"", max_bytes=1024 * 1024)

    assert _manifest(store, digest)["chunks"] == []
    assert store.load(digest) == b""


def test_capacity_is_charged_on_compressed_encrypted_bytes(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    content = b"0" * (1024 * 1024)

    digest = store.store(content, max_bytes=32 * 1024)

    assert store.load(digest) == content
    assert store.storage_bytes() < 32 * 1024


def test_manifest_chunk_digest_cannot_escape_store(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    digest = hashlib.sha256(b"target").hexdigest()
    (store.manifests / f"{digest}.json").write_text(
        json.dumps({
            "file_digest": digest,
            "original_size": 1,
            "chunks": [{"digest": "../../outside", "raw_size": 1}],
        }),
        encoding="utf-8",
    )

    with pytest.raises(BackupStoreError, match="Invalid backup content digest"):
        store.load(digest)


def test_storage_stats_expose_compression_and_deduplication(tmp_path: Path) -> None:
    store = EncryptedChunkStore(tmp_path)
    content = b"shared-and-compressible\n" * 20_000
    store.store(content, max_bytes=2 * 1024 * 1024)

    stats = store.stats()

    assert stats["encryption_enabled"] is True
    assert stats["manifest_count"] == 1
    assert stats["chunk_count"] >= 1
    assert stats["compression_ratio"] < 1
    assert stats["storage_bytes"] < stats["logical_file_bytes"]
