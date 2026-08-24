"""Credential storage abstractions with fail-safe memory fallback."""

from __future__ import annotations

from typing import Protocol


class CredentialStore(Protocol):
    def get(self, provider: str) -> str | None: ...
    def set(self, provider: str, secret: str) -> None: ...
    def delete(self, provider: str) -> None: ...


class MemoryCredentialStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self._values.get(provider)

    def set(self, provider: str, secret: str) -> None:
        if not secret:
            raise ValueError("Credential cannot be empty")
        self._values[provider] = secret

    def delete(self, provider: str) -> None:
        self._values.pop(provider, None)


class KeyringCredentialStore:
    """Optional OS credential backend; import fails closed when unavailable."""

    SERVICE = "MiniClaude"

    def __init__(self, backend=None) -> None:
        if backend is None:
            try:
                import keyring as backend
            except ImportError as exc:
                raise RuntimeError("Install miniclaude[credentials] to persist API keys") from exc
        self._backend = backend

    def get(self, provider: str) -> str | None:
        return self._backend.get_password(self.SERVICE, provider)

    def set(self, provider: str, secret: str) -> None:
        if not secret:
            raise ValueError("Credential cannot be empty")
        self._backend.set_password(self.SERVICE, provider, secret)

    def delete(self, provider: str) -> None:
        try:
            self._backend.delete_password(self.SERVICE, provider)
        except Exception:
            return
