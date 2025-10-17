"""LLM provider abstraction and a safe mock implementation.

This module intentionally avoids importing any external SDK. Real providers can
be implemented later by extending the LLMProvider interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMRequest:
    model: str
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.2


@dataclass
class LLMResponse:
    model: str
    content: str
    usage_tokens: int


class LLMProvider:
    """Provider interface for text generation."""

    async def generate(
        self, req: LLMRequest, *, user_id: str
    ) -> LLMResponse:  # pragma: no cover - interface
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    """Deterministic mock generator for development and tests."""

    async def generate(self, req: LLMRequest, *, user_id: str) -> LLMResponse:
        # No network calls. Echo with light truncation and minimal token accounting.
        content = (req.prompt or "").strip()
        if not content:
            content = "(empty prompt)"
        # Simulate token usage roughly as words + max_tokens bound
        words = content.split()
        preview = " ".join(words[: min(len(words), req.max_tokens // 3 or 1)])
        reply = f"[mock:{req.model}|temp={req.temperature}] {preview}"
        usage = min(len(words), req.max_tokens)
        return LLMResponse(model=req.model, content=reply, usage_tokens=usage)


def get_default_provider() -> LLMProvider:
    return MockLLMProvider()
