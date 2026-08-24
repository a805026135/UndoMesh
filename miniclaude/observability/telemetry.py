"""Opt-in, redacted telemetry with an OpenTelemetry adapter when installed."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import re
import time
from typing import Any, Iterator


_SENSITIVE = re.compile(r"(key|token|secret|password|authorization|prompt|content)", re.I)


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    enabled: bool = False
    service_name: str = "miniclaude"


class Telemetry:
    def __init__(self, config: TelemetryConfig = TelemetryConfig(), exporter: Any | None = None) -> None:
        self.config = config
        self.exporter = exporter
        self.records: list[dict[str, Any]] = []

    @staticmethod
    def redact(attributes: dict[str, Any]) -> dict[str, Any]:
        return {key: "[REDACTED]" if _SENSITIVE.search(key) else value for key, value in attributes.items()}

    def emit(self, name: str, attributes: dict[str, Any] | None = None, *, duration_ms: float | None = None) -> None:
        if not self.config.enabled:
            return
        record = {"name": name, "attributes": self.redact(attributes or {}), "duration_ms": duration_ms}
        self.records.append(record)
        if self.exporter is not None:
            self.exporter(record)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.emit(name, attributes, duration_ms=(time.perf_counter() - started) * 1000)
