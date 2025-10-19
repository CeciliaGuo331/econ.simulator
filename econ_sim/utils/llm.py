"""Light adapter for the project's LLM provider.

This module provides a small compatibility layer so code that previously
called `resolve_llm_provider().complete(...)` continues to work. Under the
hood we delegate to `econ_sim.utils.llm_provider.get_default_provider()`,
which in this project is backed by OpenAI and requires `OPENAI_API_KEY`.
"""

from __future__ import annotations

from typing import Optional

from .llm_provider import LLMRequest, get_default_provider


class _Adapter:
    """Adapter exposing async complete(prompt, model, max_tokens) -> str

    It delegates to the provider.generate(LLMRequest, user_id=...) and
    returns the response content as string.
    """

    def __init__(self, provider):
        self._provider = provider

    async def complete(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        req = LLMRequest(
            model=model or "gpt-3.5-turbo",
            prompt=prompt,
            max_tokens=int(max_tokens or 256),
        )
        resp = await self._provider.generate(req, user_id="api")
        return getattr(resp, "content", str(resp))


def resolve_llm_provider() -> _Adapter:
    provider = get_default_provider()
    return _Adapter(provider)
