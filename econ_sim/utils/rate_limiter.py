"""
轻量级异步速率限制器（支持 Redis 后端，回退到内存实现）。

实现说明：
- 使用固定窗口（fixed-window）计数器对 (key, window_seconds) 进行限流，
    适用于粗粒度的 API 调用控制，例如每分钟最大请求数的限制。
- 若配置了 `ECON_SIM_REDIS_URL` 并安装了 `redis.asyncio`，会使用 Redis 实现以便在多进程/多实例间共享限流计数；
    否则回退为单进程内存实现（适用于单机或测试场景）。

使用建议：
- 对于需要更平滑的速率控制或限制突发流量的场景，建议改用滑动窗口或令牌桶算法。
- 返回的 `RateLimitResult` 包含 `allowed` (是否允许)、`remaining` (本窗口剩余可用次数)
    与 `reset_seconds` (窗口剩余秒数) 等信息，便于前端友好提示。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional, Any

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
        self._redis: Optional[Any] = None
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


# 轻量级的令牌桶限流（占位注释）。如需更精细的限流策略，可在此模块中添加实现。
