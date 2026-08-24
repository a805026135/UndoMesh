"""BYOK provider presets, persistence, and connection checks."""

from __future__ import annotations

import json

import pytest

from miniclaude.core.config import MiniClaudeConfig
from miniclaude.llm.client import ClaudeClient
from miniclaude.llm.diagnostics import ConnectionStatus, classify_connection_error
from miniclaude.llm.provider_settings import ProviderSettingsStore
from miniclaude.llm.providers import PROVIDERS, resolve_base_url


def test_mainstream_provider_presets_have_secure_urls() -> None:
    assert {"openai", "deepseek", "anthropic", "gemini", "dashscope", "kimi", "custom"} <= set(PROVIDERS)
    for name, preset in PROVIDERS.items():
        if name != "custom":
            assert preset.base_url.startswith("https://")
            assert preset.models


def test_custom_url_allows_https_and_localhost_only() -> None:
    assert resolve_base_url("custom", "https://llm.example/v1") == "https://llm.example/v1"
    assert resolve_base_url("custom", "http://localhost:11434/v1") == "http://localhost:11434/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        resolve_base_url("custom", "http://remote.example/v1")


def test_provider_settings_never_persist_api_key(tmp_path) -> None:
    path = tmp_path / "provider.json"
    store = ProviderSettingsStore(path)
    store.save(provider="deepseek", model="deepseek-v4-flash", api_base_url="")

    text = path.read_text(encoding="utf-8")
    assert "api_key" not in text
    assert store.load()["provider"] == "deepseek"


def test_provider_specific_environment_key(monkeypatch) -> None:
    monkeypatch.delenv("MINICLAUDE_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    config = MiniClaudeConfig(provider="deepseek", api_base_url="")
    assert config.api_key == "secret"
    assert config.effective_api_base_url == "https://api.deepseek.com"


@pytest.mark.asyncio
async def test_connection_uses_minimal_chat_request(config, monkeypatch) -> None:
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            message = type("Message", (), {"content": "OK", "tool_calls": None})()
            choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
            return type("Response", (), {"choices": [choice], "usage": None})()

    client = ClaudeClient(config)
    client._client.chat.completions = FakeCompletions()
    response = await client.test_connection()

    assert response.text == "OK"
    assert calls[0]["max_tokens"] == 8
    assert calls[0]["temperature"] == 0


def test_connection_errors_are_classified_without_raw_provider_details() -> None:
    class ProviderFailure(Exception):
        status_code = 401

    status, message = classify_connection_error(ProviderFailure("secret account detail"))
    assert status == ConnectionStatus.AUTHENTICATION
    assert "secret" not in message

    status, message = classify_connection_error(Exception("insufficient balance for org-private"))
    assert status == ConnectionStatus.QUOTA
    assert "org-private" not in message


@pytest.mark.asyncio
async def test_diagnostic_report_is_structured(config) -> None:
    client = ClaudeClient(config)

    async def failed():
        raise Exception("model_not_found and internal account id")

    client.test_connection = failed
    report = await client.diagnose_connection()
    assert not report.success
    assert report.status == ConnectionStatus.MODEL
    assert "account id" not in report.message
