"""Shared test fixtures for UndoMesh tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from undomesh.core.config import UndoMeshConfig, reset_config
from undomesh.llm.client import LLMResponse


class ScriptedLLMClient:
    """Deterministic offline LLM double for query-loop integration tests."""

    def __init__(self, responses: list[LLMResponse | BaseException]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedLLMClient received an unexpected request")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        self.total_input_tokens += item.input_tokens
        self.total_output_tokens += item.output_tokens
        return item

    async def close(self) -> None:
        return None


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with some test files."""
    # Create test files
    (tmp_path / "hello.py").write_text('print("Hello, World!")\n')
    (tmp_path / "utils.py").write_text(
        'def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n'
    )
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.py").write_text("# nested file\nx = 42\n")
    return tmp_path


@pytest.fixture
def config(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> UndoMeshConfig:
    """Create a test configuration."""
    monkeypatch.setenv("UNDOMESH_API_KEY", "test-key-for-testing")
    monkeypatch.setenv("UNDOMESH_PROJECT_DIR", str(tmp_project))
    monkeypatch.setenv("UNDOMESH_MEMORY_ENABLED", "false")
    monkeypatch.setenv("UNDOMESH_ALLOW_SHELL", "true")
    reset_config()
    cfg = UndoMeshConfig()
    yield cfg
    reset_config()


@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """Create a mock LLM client."""
    client = AsyncMock()
    client.total_input_tokens = 0
    client.total_output_tokens = 0
    client.total_tokens = 0
    return client
