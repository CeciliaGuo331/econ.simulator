"""LLM provider abstraction.

This module provides a thin adapter to an OpenAI-compatible provider. The
implementation intentionally avoids importing any external SDK at module load
time (the OpenAI SDK is imported only when the provider is requested). If you
need a local mock for testing, add one in tests or replace the provider via
dependency injection; the project no longer assumes a built-in mock provider.
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
