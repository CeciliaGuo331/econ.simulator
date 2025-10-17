"""Simple async rate limiter with Redis backend (fallback in-memory).

Use a fixed-window counter per (key, window_sec). Suitable for coarse-grained
API call limiting. For stricter guarantees, consider sliding windows or token
bucket variants.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

try:  # optional dependency
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_seconds: int


class RateLimiter:
    def __init__(
        self, *, window_seconds: int, max_calls: int, prefix: str = "econ_sim:rl"
    ) -> None:
        self.window = int(window_seconds)
        self.max_calls = int(max_calls)
        self.prefix = prefix
        self._redis: Optional[Redis] = None
        self._memory: dict[str, tuple[int, int]] = {}
        self._lock = asyncio.Lock()

        url = os.getenv("ECON_SIM_REDIS_URL")
        if url and Redis is not None:
            self._redis = Redis.from_url(url, encoding="utf-8", decode_responses=True)

    def _key(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    async def check(self, name: str) -> RateLimitResult:
        if self._redis is not None:
            return await self._check_redis(name)
        return await self._check_memory(name)

    async def _check_redis(self, name: str) -> RateLimitResult:
        assert self._redis is not None
        import time

        now = int(time.time())
        window_start = now - (now % self.window)
        key = self._key(f"{name}:{window_start}")
        # INCR and set expiry atomically using pipeline
        async with self._redis.pipeline(transaction=True) as pipe:  # type: ignore
            pipe.incr(key, 1)
            pipe.expire(key, self.window + 2)
            calls, _ = await pipe.execute()
        calls = int(calls or 0)
        remaining = max(0, self.max_calls - calls)
        allowed = calls <= self.max_calls
        reset = self.window - (now - window_start)
        return RateLimitResult(
            allowed=allowed, remaining=remaining, reset_seconds=int(reset)
        )

    async def _check_memory(self, name: str) -> RateLimitResult:
        import time

        now = int(time.time())
        window_start = now - (now % self.window)
        key = self._key(f"{name}:{window_start}")
        async with self._lock:
            count, ts = self._memory.get(key, (0, window_start))
            if ts != window_start:
                count = 0
                ts = window_start
            count += 1
            self._memory[key] = (count, ts)
        remaining = max(0, self.max_calls - count)
        allowed = count <= self.max_calls
        reset = self.window - (now - window_start)
        return RateLimitResult(
            allowed=allowed, remaining=remaining, reset_seconds=int(reset)
        )


"""Lightweight async-safe token bucket rate limiter.

Per-user limiter with burst and refill rate. In-memory only; can be swapped
to Redis later.
"""
