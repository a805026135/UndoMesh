"""Optional native compression/chunking backend with deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass
import zlib


@dataclass(frozen=True, slots=True)
class BackendInfo:
    chunker: str
    compressor: str
    native: bool


class CompressionBackend:
    def __init__(self) -> None:
        try:
            import zstandard as zstd  # type: ignore
        except ImportError:
            self._zstd = None
        else:
            self._zstd = zstd
        try:
            import fastcdc  # type: ignore
        except ImportError:
            self._fastcdc = None
        else:
            self._fastcdc = fastcdc

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            "fastcdc" if self._fastcdc else "python-gear",
            "zstandard" if self._zstd else "zlib",
            bool(self._fastcdc and self._zstd),
        )

    def compress(self, data: bytes) -> bytes:
        return self._zstd.ZstdCompressor(level=3).compress(data) if self._zstd else zlib.compress(data)

    def decompress(self, data: bytes, *, max_output_size: int) -> bytes:
        if self._zstd:
            return self._zstd.ZstdDecompressor().decompress(data, max_output_size=max_output_size)
        value = zlib.decompress(data)
        if len(value) > max_output_size:
            raise ValueError("decompressed output exceeds limit")
        return value
