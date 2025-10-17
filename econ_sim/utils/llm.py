"""LLM provider abstraction with a safe mock implementation.

Default provider is a mock that echoes input. Real providers can be added by
implementing the LLMProvider interface and wiring via environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol


class LLMProvider(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str: ...


@dataclass
class MockLLMProvider:
    prefix: str = "Echo"

    async def complete(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        text = prompt.strip()
        if max_tokens is not None and max_tokens > 0:
            # naive token cap by characters for mock
            text = text[: max(8, max_tokens)]
        return f"{self.prefix}: {text}"


def resolve_llm_provider() -> LLMProvider:
    # Placeholder for future real provider, controlled by env vars
    provider = os.getenv("ECON_SIM_LLM_PROVIDER", "mock").lower()
    if provider == "mock":
        return MockLLMProvider()
    # Fallback to mock for unknown providers
    return MockLLMProvider(prefix="LLM")
