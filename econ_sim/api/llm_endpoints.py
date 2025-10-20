"""LLM API endpoints with per-user rate limiting.

This provides a minimal completion endpoint that delegates to the configured
OpenAI-compatible provider and enforces a per-user quota via Redis/in-memory
fixed window limiting.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import user_manager
from ..auth.user_manager import UserProfile
from ..utils.llm import resolve_llm_provider
from ..utils.rate_limiter import RateLimiter


router = APIRouter(prefix="/llm", tags=["llm"])


async def get_current_user(authorization: str) -> UserProfile:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )
    profile = await user_manager.get_profile_by_token(token.strip())
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        )
    return profile


class CompletionRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None
    max_tokens: Optional[int] = Field(default=256, ge=1, le=2048)


class CompletionResponse(BaseModel):
    output: str
    model: str
    usage_tokens: int
    rate_remaining: int
    rate_reset_seconds: int


_limiter = RateLimiter(window_seconds=60, max_calls=30, prefix="econ_sim:rl:llm")


@router.post("/completions", response_model=CompletionResponse)
async def completions(
    payload: CompletionRequest,
    user: UserProfile = Depends(get_current_user),
) -> CompletionResponse:
    # enforce per-user rate limit
    rl = await _limiter.check(f"{user.email}")
    if not rl.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {rl.reset_seconds}s.",
        )
    provider = resolve_llm_provider()
    text = await provider.complete(
        payload.prompt, model=payload.model, max_tokens=payload.max_tokens
    )
    # Simple token usage estimate by characters (placeholder)
    usage = min(len(payload.prompt) // 4 + (payload.max_tokens or 0), 4096)
    return CompletionResponse(
        output=text,
        model=payload.model or "openai",
        usage_tokens=usage,
        rate_remaining=rl.remaining,
        rate_reset_seconds=rl.reset_seconds,
    )
