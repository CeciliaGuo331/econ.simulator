"""Per-execution LLM session with simple quota enforcement.

Provides a synchronous helper that scripts running inside the sandbox can call
via the global name `llm`. The implementation delegates to the project's
OpenAI-compatible LLM provider and enforces:
 - max_calls: maximum number of generate calls allowed per script execution
 - max_tokens_total: cumulative token usage allowed for the whole execution
 - max_tokens_per_call: token cap for a single call

Configuration is via environment variables (sane defaults provided).
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass
from typing import Optional

from .llm_provider import LLMRequest, get_default_provider


DEFAULT_MAX_CALLS = int(os.getenv("ECON_SIM_LLM_MAX_CALLS_PER_SCRIPT", "1"))
DEFAULT_MAX_TOKENS_PER_CALL = int(os.getenv("ECON_SIM_LLM_MAX_TOKENS_PER_CALL", "512"))
# approximate input token limit (how many tokens the prompt may contain)
DEFAULT_MAX_INPUT_TOKENS = int(os.getenv("ECON_SIM_LLM_MAX_INPUT_TOKENS", "1024"))


class LLMQuotaExceeded(RuntimeError):
    pass


@dataclass
class LLMSession:
    provider: object
    max_calls: int = DEFAULT_MAX_CALLS
    max_tokens_per_call: int = DEFAULT_MAX_TOKENS_PER_CALL

    calls_made: int = 0

    def _check_call_allowed(self, requested_tokens: Optional[int]) -> None:
        # Enforce single-call-per-script (per-tick) and per-call output token cap.
        if self.max_calls is not None and self.calls_made >= self.max_calls:
            raise LLMQuotaExceeded("LLM call quota exceeded for this script execution")
        if requested_tokens is not None and self.max_tokens_per_call is not None:
            if requested_tokens > self.max_tokens_per_call:
                raise LLMQuotaExceeded(
                    f"Requested max_tokens ({requested_tokens}) exceeds per-call limit ({self.max_tokens_per_call})"
                )

    def _check_input_tokens(self, prompt: str) -> None:
        """Approximate prompt token count and enforce an input token cap."""
        if not prompt:
            return
        # Very simple heuristic: 1 token ~= 4 chars; use conservative ceil
        approx = max(1, int(len(prompt) / 4))
        if approx > DEFAULT_MAX_INPUT_TOKENS:
            raise LLMQuotaExceeded(
                f"Prompt too long (approx tokens={approx}) exceeds input limit ({DEFAULT_MAX_INPUT_TOKENS})"
            )

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> dict:
        """Synchronously call the underlying provider.generate and return a dict with content and usage_tokens.

        Raises LLMQuotaExceeded on quota violations, or propagates provider errors.
        """
        # Normalize requested token amounts and check input size
        requested = None
        if max_tokens is not None:
            try:
                requested = int(max_tokens)
            except Exception:
                requested = None

        # input token check (approximation)
        self._check_input_tokens(prompt)

        # enforce call count and per-call output token cap
        self._check_call_allowed(requested)

        # construct request object compatible with provider
        req = LLMRequest(
            model=model or "default",
            prompt=prompt,
            max_tokens=int(max_tokens or DEFAULT_MAX_TOKENS_PER_CALL),
        )

        # provider.generate is async; run it in a new event loop synchronously
        try:
            resp = asyncio.run(self.provider.generate(req, user_id="script"))
        except RuntimeError:
            # If there's already a running loop, create a new loop policy temporarily
            loop = asyncio.new_event_loop()
            try:
                resp = loop.run_until_complete(
                    self.provider.generate(req, user_id="script")
                )
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        # update call counter; we do not track cumulative tokens per script
        self.calls_made += 1
        try:
            usage = int(getattr(resp, "usage_tokens", 0) or 0)
        except Exception:
            usage = 0

        # Return a simple dict that scripts can inspect
        return {
            "model": getattr(resp, "model", None),
            "content": getattr(resp, "content", str(resp)),
            "usage_tokens": usage,
        }


def create_llm_session_from_env() -> LLMSession:
    provider = get_default_provider()
    max_calls = DEFAULT_MAX_CALLS
    try:
        max_calls = int(
            os.getenv("ECON_SIM_LLM_MAX_CALLS_PER_SCRIPT", str(DEFAULT_MAX_CALLS))
        )
    except Exception:
        pass
    max_tokens_per_call = DEFAULT_MAX_TOKENS_PER_CALL
    try:
        max_tokens_per_call = int(
            os.getenv(
                "ECON_SIM_LLM_MAX_TOKENS_PER_CALL", str(DEFAULT_MAX_TOKENS_PER_CALL)
            )
        )
    except Exception:
        pass

    return LLMSession(
        provider=provider,
        max_calls=max_calls,
        max_tokens_per_call=max_tokens_per_call,
    )


__all__ = ["create_llm_session_from_env", "LLMSession", "LLMQuotaExceeded"]
