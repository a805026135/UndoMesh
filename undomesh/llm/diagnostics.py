"""Secret-safe provider connection diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ConnectionStatus(StrEnum):
    OK = "ok"
    AUTHENTICATION = "authentication_error"
    QUOTA = "quota_error"
    MODEL = "model_error"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network_error"
    PROVIDER = "provider_error"


@dataclass(frozen=True, slots=True)
class ConnectionDiagnostic:
    success: bool
    status: ConnectionStatus
    provider: str
    model: str
    base_url: str
    latency_ms: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_connection_error(exc: Exception) -> tuple[ConnectionStatus, str]:
    text = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code == 401 or "invalid_api_key" in text or "incorrect api key" in text:
        return ConnectionStatus.AUTHENTICATION, "API key is invalid for this endpoint or region"
    if "insufficient balance" in text or "exceeded_current_quota" in text or "quota" in text:
        return ConnectionStatus.QUOTA, "Account balance or quota is insufficient"
    if "model_not_found" in text or "does not exist" in text or "unknown model" in text:
        return ConnectionStatus.MODEL, "Model is unavailable for this account or endpoint"
    if status_code == 429 or "rate limit" in text:
        return ConnectionStatus.RATE_LIMIT, "Provider rate limit was reached"
    if any(marker in text for marker in ("connection", "dns", "timeout", "timed out")):
        return ConnectionStatus.NETWORK, "Could not reach the provider endpoint"
    return ConnectionStatus.PROVIDER, "Provider rejected the request"
