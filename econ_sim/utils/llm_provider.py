"""LLM provider abstraction and a safe mock implementation.

This module intentionally avoids importing any external SDK. Real providers can
be implemented later by extending the LLMProvider interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import os
import logging


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

    async def generate(self, req: LLMRequest, *, user_id: str) -> LLMResponse:
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
    """Return an OpenAI-backed provider. Requires OPENAI_API_KEY and the `openai` package.

    Raises RuntimeError with a helpful message if configuration is missing.
    """
    try:
        import openai  # type: ignore
    except Exception as exc:  # pragma: no cover - diagnostic
        logging.exception("openai package import failed")
        raise RuntimeError(
            "The 'openai' package is required for the OpenAI LLM provider. Install it with 'pip install openai'."
        ) from exc

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set; OpenAI provider cannot be used"
        )

    openai.api_key = key

    class OpenAIProvider(LLMProvider):
        async def generate(self, req: LLMRequest, *, user_id: str) -> LLMResponse:
            # Use ChatCompletion for gpt-like models, fallback to Completion for older models
            try:
                if req.model and "gpt" in req.model:
                    resp = openai.ChatCompletion.create(
                        model=req.model,
                        messages=[{"role": "user", "content": req.prompt}],
                        max_tokens=req.max_tokens,
                        temperature=req.temperature,
                    )
                    # compatibility: some SDKs return nested objects
                    choice = resp.choices[0]
                    text = (
                        getattr(choice.message, "content", None)
                        or choice["message"]["content"]
                    )
                    usage = (
                        int(resp.usage.get("total_tokens", 0))
                        if getattr(resp, "usage", None)
                        else 0
                    )
                else:
                    resp = openai.Completion.create(
                        model=req.model or "text-davinci-003",
                        prompt=req.prompt,
                        max_tokens=req.max_tokens,
                        temperature=req.temperature,
                    )
                    choice = resp.choices[0]
                    text = choice.text
                    usage = (
                        int(resp.usage.get("total_tokens", 0))
                        if getattr(resp, "usage", None)
                        else 0
                    )
                return LLMResponse(
                    model=req.model or "openai", content=text, usage_tokens=usage
                )
            except Exception:
                logging.exception("OpenAI provider generate failed")
                raise

    return OpenAIProvider()
