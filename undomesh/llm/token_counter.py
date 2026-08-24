"""Token counting utilities using tiktoken."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

# Claude models use a tokenizer close to cl100k_base
_FALLBACK_ENCODING = "cl100k_base"


@lru_cache(maxsize=4)
def _get_encoding(model: str = "claude") -> Any | None:
    """Get an encoding when locally available, without requiring the network.

    ``tiktoken`` may download encoding data on first use. Token counting is a
    best-effort local estimate, so unavailable encoding data must not make the
    agent or its offline tests fail.
    """
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        try:
            return tiktoken.get_encoding(_FALLBACK_ENCODING)
        except Exception as exc:
            logger.debug("Tokenizer unavailable; using deterministic estimate: %s", exc)
            return None


def _estimate_tokens(text: str) -> int:
    """Deterministic model-agnostic fallback used when no tokenizer is cached."""
    if not text:
        return 0
    # UTF-8 bytes/4 is a conservative-enough approximation for English while
    # naturally assigning more weight to CJK text. Always count non-empty text.
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def count_tokens(text: str, model: str = "claude") -> int:
    """Count approximate tokens in a text string.

    Args:
        text: The text to count tokens for.
        model: Model name (used to select tokenizer).

    Returns:
        Approximate token count.
    """
    if not text:
        return 0
    enc = _get_encoding(model)
    if enc is None:
        return _estimate_tokens(text)
    return len(enc.encode(text))


def count_message_tokens(
    messages: list[dict],
    model: str = "claude",
) -> int:
    """Count approximate tokens in a list of messages.

    Each message has overhead for role/formatting. We estimate ~4 tokens
    per message for overhead.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        model: Model name.

    Returns:
        Approximate total token count.
    """
    total = 0
    for msg in messages:
        total += 4  # message overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        total += count_tokens(text, model)
                elif isinstance(block, str):
                    total += count_tokens(block, model)
    return total


def truncate_to_tokens(text: str, max_tokens: int, model: str = "claude") -> str:
    """Truncate text to fit within a token budget.

    Args:
        text: Text to truncate.
        max_tokens: Maximum tokens allowed.
        model: Model name.

    Returns:
        Truncated text with '...' suffix if truncated.
    """
    if not text:
        return ""
    enc = _get_encoding(model)
    if enc is None:
        if _estimate_tokens(text) <= max_tokens:
            return text
        byte_budget = max(0, max_tokens * 4)
        truncated = text.encode("utf-8")[:byte_budget].decode("utf-8", errors="ignore")
        return truncated + "\n...[truncated]"
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated = enc.decode(tokens[:max_tokens])
    return truncated + "\n...[truncated]"
