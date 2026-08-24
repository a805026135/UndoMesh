"""Offline behavior for approximate token counting."""

from __future__ import annotations

from undomesh.llm import token_counter


def test_token_count_falls_back_without_network(monkeypatch):
    token_counter._get_encoding.cache_clear()

    def unavailable(*args, **kwargs):
        raise OSError("network disabled")

    monkeypatch.setattr(token_counter.tiktoken, "encoding_for_model", unavailable)
    monkeypatch.setattr(token_counter.tiktoken, "get_encoding", unavailable)

    assert token_counter.count_tokens("hello world") > 0
    assert token_counter.truncate_to_tokens("x" * 100, 5).endswith("...[truncated]")
    token_counter._get_encoding.cache_clear()
