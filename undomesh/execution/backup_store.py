"""Compressed, chunk-deduplicated and authenticated transaction backup storage."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from typing import BinaryIO, Iterator
import zlib
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class BackupStoreError(RuntimeError):
    pass


class BackupBudgetError(BackupStoreError):
    pass


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    minimum: int = 16 * 1024
    average: int = 64 * 1024
    maximum: int = 256 * 1024


class EncryptedChunkStore:
    """Whole-file manifests over content-defined, compressed, encrypted chunks."""

    MAGIC = b"MCC1"
    GEAR = tuple(
        int.from_bytes(hashlib.sha256(b"undomesh-gear-v1:" + bytes([value])).digest()[:8], "big")
        for value in range(256)
    )

    def __init__(
        self,
        root: Path,
        *,
        encryption: bool = True,
        encryption_key: bytes | None = None,
        chunking: ChunkingPolicy | None = None,
    ) -> None:
        self.root = root
        self.legacy_blobs = root / "blobs"
        self.chunks = root / "chunks"
        self.manifests = root / "manifests"
        self.keys = root / "keys"
        self.trash = root / "trash"
        self.encryption = encryption
        self._provided_key = encryption_key
        self.chunking = chunking or ChunkingPolicy()
        for directory in (self.legacy_blobs, self.chunks, self.manifests, self.keys, self.trash):
            directory.mkdir(parents=True, exist_ok=True)
            self._chmod(directory, 0o700)

    def store(self, content: bytes, *, max_bytes: int) -> str:
        """Compatibility wrapper for callers that already hold small content."""
        return self.store_stream(BytesIO(content), max_bytes=max_bytes)

    def store_path(self, path: Path, *, max_bytes: int) -> str:
        """Store a file without loading it in full."""
        with path.open("rb") as handle:
            return self.store_stream(handle, max_bytes=max_bytes)

    def store_stream(self, source: BinaryIO, *, max_bytes: int) -> str:
        """Stream source into authenticated chunks and publish its manifest last."""
        file_hash = hashlib.sha256()
        original_size = 0
        chunks = []
        for raw in self.iter_chunks(source):
            file_hash.update(raw)
            original_size += len(raw)
            digest = hashlib.sha256(raw).hexdigest()
            chunk_path = self.chunks / f"{digest}.chunk"
            if not chunk_path.exists():
                encoded = self._encode_chunk(digest, raw)
                self._ensure_capacity(len(encoded), max_bytes)
                self._atomic_write(chunk_path, encoded)
            chunks.append({"digest": digest, "raw_size": len(raw)})

        file_digest = file_hash.hexdigest()
        manifest_path = self.manifests / f"{file_digest}.json"
        if manifest_path.exists() or (self.legacy_blobs / file_digest).exists():
            return file_digest

        manifest = {
            "schema_version": 1,
            "file_digest": file_digest,
            "original_size": original_size,
            "chunking": {
                "algorithm": "gear-v1",
                "minimum": self.chunking.minimum,
                "average": self.chunking.average,
                "maximum": self.chunking.maximum,
            },
            "chunks": chunks,
        }
        encoded_manifest = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._ensure_capacity(len(encoded_manifest), max_bytes)
        self._atomic_write(manifest_path, encoded_manifest)
        return file_digest

    def load(self, file_digest: str) -> bytes:
        """Compatibility wrapper; large-file restores should use write_to()."""
        output = BytesIO()
        self.write_to(file_digest, output)
        return output.getvalue()

    def write_to(self, file_digest: str, destination: BinaryIO) -> int:
        """Verify and stream one stored file to an already-open destination."""
        total = 0
        for raw in self.iter_file(file_digest):
            destination.write(raw)
            total += len(raw)
        return total

    def iter_file(self, file_digest: str) -> Iterator[bytes]:
        """Yield verified plaintext chunks and verify the complete file digest."""
        self._validate_digest(file_digest)
        manifest_path = self.manifests / f"{file_digest}.json"
        if not manifest_path.exists():
            legacy = self.legacy_blobs / file_digest
            hasher = hashlib.sha256()
            with legacy.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    hasher.update(block)
                    yield block
            if hasher.hexdigest() != file_digest:
                raise BackupStoreError(f"Legacy backup blob failed integrity check: {file_digest}")
            return

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("file_digest") != file_digest:
            raise BackupStoreError(f"Backup manifest identity mismatch: {file_digest}")
        file_hash = hashlib.sha256()
        total = 0
        for item in manifest.get("chunks", []):
            digest = str(item["digest"])
            self._validate_digest(digest)
            encoded = (self.chunks / f"{digest}.chunk").read_bytes()
            expected_size = int(item["raw_size"])
            if expected_size < 0 or expected_size > self.chunking.maximum:
                raise BackupStoreError(f"Backup chunk declares an invalid size: {digest}")
            raw = self._decode_chunk(digest, encoded, expected_size)
            if len(raw) != expected_size:
                raise BackupStoreError(f"Backup chunk size mismatch: {digest}")
            file_hash.update(raw)
            total += len(raw)
            yield raw
        if total != int(manifest.get("original_size", -1)):
            raise BackupStoreError(f"Backup file size mismatch: {file_digest}")
        if file_hash.hexdigest() != file_digest:
            raise BackupStoreError(f"Backup file failed integrity check: {file_digest}")

    def split(self, content: bytes) -> tuple[bytes, ...]:
        return tuple(self.iter_chunks(BytesIO(content)))

    def iter_chunks(self, source: BinaryIO, *, read_size: int = 1024 * 1024) -> Iterator[bytes]:
        """Content-defined chunking with memory bounded by one maximum-size chunk."""
        minimum = self.chunking.minimum
        maximum = self.chunking.maximum
        average = self.chunking.average
        if minimum <= 0 or minimum > average or average > maximum or average & (average - 1):
            raise BackupStoreError("Chunk sizes must satisfy 0 < min <= power-of-two average <= max")
        mask = average - 1
        fingerprint = 0
        current = bytearray()
        while block := source.read(read_size):
            for value in block:
                current.append(value)
                fingerprint = ((fingerprint << 1) + self.GEAR[value]) & 0xFFFFFFFFFFFFFFFF
                size = len(current)
                if size >= minimum and ((fingerprint & mask) == 0 or size >= maximum):
                    yield bytes(current)
                    current.clear()
                    fingerprint = 0
        if current:
            yield bytes(current)

    def contains(self, file_digest: str) -> bool:
        self._validate_digest(file_digest)
        return (self.manifests / f"{file_digest}.json").exists() or (self.legacy_blobs / file_digest).exists()

    def original_size(self, file_digest: str) -> int:
        """Return the logical size recorded for one stored file."""
        self._validate_digest(file_digest)
        manifest = self.manifests / f"{file_digest}.json"
        if manifest.exists():
            value = json.loads(manifest.read_text(encoding="utf-8"))
            if value.get("file_digest") != file_digest:
                raise BackupStoreError(f"Backup manifest identity mismatch: {file_digest}")
            return int(value.get("original_size", -1))
        return (self.legacy_blobs / file_digest).stat().st_size

    def referenced_chunks(self, file_digests: set[str]) -> set[str]:
        result = set()
        for digest in file_digests:
            self._validate_digest(digest)
            manifest = self.manifests / f"{digest}.json"
            if not manifest.exists():
                continue
            value = json.loads(manifest.read_text(encoding="utf-8"))
            for item in value.get("chunks", []):
                chunk_digest = str(item["digest"])
                self._validate_digest(chunk_digest)
                result.add(chunk_digest)
        return result

    def storage_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for directory in (self.legacy_blobs, self.chunks, self.manifests, self.trash)
            for path in directory.iterdir()
            if path.is_file()
        )

    def stats(self) -> dict[str, int | float | bool]:
        logical_bytes = 0
        unique_raw: dict[str, int] = {}
        for path in self.manifests.glob("*.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            logical_bytes += int(manifest.get("original_size", 0))
            for item in manifest.get("chunks", []):
                unique_raw[str(item["digest"])] = int(item["raw_size"])
        chunk_bytes = sum(path.stat().st_size for path in self.chunks.glob("*.chunk"))
        trash_bytes = sum(path.stat().st_size for path in self.trash.iterdir() if path.is_file())
        unique_raw_bytes = sum(unique_raw.values())
        return {
            "encryption_enabled": self.encryption,
            "manifest_count": len(list(self.manifests.glob("*.json"))),
            "chunk_count": len(list(self.chunks.glob("*.chunk"))),
            "logical_file_bytes": logical_bytes,
            "unique_chunk_raw_bytes": unique_raw_bytes,
            "chunk_storage_bytes": chunk_bytes,
            "trash_bytes": trash_bytes,
            "storage_bytes": self.storage_bytes(),
            "deduplicated_bytes": max(0, logical_bytes - unique_raw_bytes),
            "compression_ratio": round(chunk_bytes / unique_raw_bytes, 4) if unique_raw_bytes else 0.0,
        }

    def _encode_chunk(self, digest: str, raw: bytes) -> bytes:
        compressed = zlib.compress(raw, level=6)
        use_compression = len(compressed) < len(raw)
        payload = compressed if use_compression else raw
        flags = 1 if use_compression else 0
        key_id = b""
        nonce = b""
        if self.encryption:
            key_id_text, key = self._active_key()
            key_id = key_id_text.encode("ascii")
            nonce = os.urandom(12)
            payload = AESGCM(key).encrypt(nonce, payload, self._aad(digest))
            flags |= 2
        if len(key_id) > 255 or len(nonce) > 255:
            raise BackupStoreError("Chunk encryption header is too large")
        return self.MAGIC + bytes((flags, len(key_id), len(nonce))) + key_id + nonce + payload

    def _decode_chunk(self, digest: str, encoded: bytes, expected_size: int) -> bytes:
        if len(encoded) < 7 or encoded[:4] != self.MAGIC:
            raise BackupStoreError(f"Unsupported backup chunk format: {digest}")
        flags, key_length, nonce_length = encoded[4], encoded[5], encoded[6]
        if flags & ~3:
            raise BackupStoreError(f"Unsupported backup chunk flags: {digest}")
        offset = 7
        key_id = encoded[offset:offset + key_length].decode("ascii")
        offset += key_length
        nonce = encoded[offset:offset + nonce_length]
        offset += nonce_length
        payload = encoded[offset:]
        if flags & 2:
            if nonce_length != 12 or not key_id:
                raise BackupStoreError(f"Invalid encrypted chunk header: {digest}")
            key = self._read_key(key_id)
            try:
                payload = AESGCM(key).decrypt(nonce, payload, self._aad(digest))
            except Exception as exc:
                raise BackupStoreError(f"Backup chunk authentication failed: {digest}") from exc
        if flags & 1:
            try:
                decompressor = zlib.decompressobj()
                payload = decompressor.decompress(payload, expected_size + 1)
                if len(payload) > expected_size or not decompressor.eof or decompressor.unused_data:
                    raise BackupStoreError(f"Backup chunk decompression bounds failed: {digest}")
            except zlib.error as exc:
                raise BackupStoreError(f"Backup chunk decompression failed: {digest}") from exc
        if hashlib.sha256(payload).hexdigest() != digest:
            raise BackupStoreError(f"Backup chunk failed integrity check: {digest}")
        return payload

    def _active_key(self) -> tuple[str, bytes]:
        if self._provided_key is not None:
            if len(self._provided_key) != 32:
                raise BackupStoreError("Backup encryption key must contain exactly 32 bytes")
            key = self._provided_key
            key_id = hashlib.sha256(key).hexdigest()[:16]
            return key_id, key
        pointer = self.keys / "active"
        if pointer.exists():
            key_id = pointer.read_text(encoding="ascii").strip()
            return key_id, self._read_key(key_id)
        key = AESGCM.generate_key(bit_length=256)
        key_id = hashlib.sha256(key).hexdigest()[:16]
        self._atomic_write(self.keys / f"{key_id}.key", key)
        self._atomic_write(pointer, key_id.encode("ascii"))
        return key_id, key

    def _read_key(self, key_id: str) -> bytes:
        if not key_id or any(value not in "0123456789abcdef" for value in key_id):
            raise BackupStoreError("Invalid backup key identifier")
        if self._provided_key is not None:
            expected = hashlib.sha256(self._provided_key).hexdigest()[:16]
            if key_id != expected:
                raise BackupStoreError(f"Required backup key is unavailable: {key_id}")
            return self._provided_key
        key = (self.keys / f"{key_id}.key").read_bytes()
        if len(key) != 32 or hashlib.sha256(key).hexdigest()[:16] != key_id:
            raise BackupStoreError(f"Backup key failed integrity check: {key_id}")
        return key

    def _ensure_capacity(self, additional: int, maximum: int) -> None:
        if self.storage_bytes() + additional > maximum:
            raise BackupBudgetError(
                "Transaction backup budget exceeded; finalize and prune old transactions before retrying"
            )

    @staticmethod
    def _aad(digest: str) -> bytes:
        return b"undomesh-chunk-v1:" + digest.encode("ascii")

    @staticmethod
    def _validate_digest(digest: str) -> None:
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise BackupStoreError("Invalid backup content digest")

    @classmethod
    def _atomic_write(cls, path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        cls._chmod(path, 0o600)
        cls._fsync_directory(path.parent)

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
