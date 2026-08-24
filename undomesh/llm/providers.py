"""Provider presets for the OpenAI-compatible UndoMesh transport."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    provider_id: str
    display_name: str
    base_url: str
    models: tuple[str, ...]
    api_key_env: tuple[str, ...]
    compatibility_note: str = ""
    tool_calling: bool = True
    compatibility_tier: str = "openai-compatible"


PROVIDERS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset("openai", "OpenAI", "https://api.openai.com/v1", ("gpt-5.1", "gpt-5-mini", "gpt-4.1"), ("OPENAI_API_KEY",), compatibility_tier="native"),
    "deepseek": ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com", ("deepseek-v4-pro", "deepseek-v4-flash"), ("DEEPSEEK_API_KEY",)),
    "anthropic": ProviderPreset("anthropic", "Anthropic Claude", "https://api.anthropic.com/v1/", ("claude-opus-5", "claude-sonnet-4-6"), ("ANTHROPIC_API_KEY",), "Uses Anthropic's OpenAI SDK compatibility layer; some native features are unavailable.", compatibility_tier="official-compatibility"),
    "gemini": ProviderPreset("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", ("gemini-3.7-flash",), ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "Uses Google's OpenAI compatibility layer, currently documented as beta.", compatibility_tier="official-compatibility-beta"),
    "dashscope": ProviderPreset("dashscope", "Alibaba Model Studio (China)", "https://dashscope.aliyuncs.com/compatible-mode/v1", ("qwen3.7-plus", "qwen3.7-max", "qwen3.6-flash"), ("DASHSCOPE_API_KEY",)),
    "kimi": ProviderPreset("kimi", "Kimi / Moonshot AI (China)", "https://api.moonshot.cn/v1", ("kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k3", "kimi-k2.6", "kimi-k2.5"), ("KIMI_API_KEY", "MOONSHOT_API_KEY")),
    "custom": ProviderPreset("custom", "Custom OpenAI-compatible", "", (), ("UNDOMESH_API_KEY",), "The endpoint must implement OpenAI-compatible chat completions and tool calls."),
}


def get_provider(provider_id: str) -> ProviderPreset:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown model provider: {provider_id}") from exc


def resolve_base_url(provider_id: str, custom_base_url: str = "") -> str:
    preset = get_provider(provider_id)
    value = custom_base_url.strip() or preset.base_url
    if not value:
        raise ValueError("A Base URL is required for a custom provider")
    if not value.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("Base URL must use HTTPS (HTTP is allowed only for localhost)")
    return value.rstrip("/") + ("/" if preset.base_url.endswith("/") else "")
