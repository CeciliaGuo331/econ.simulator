"""Shared PostgreSQL connection pooling utilities."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Tuple, TYPE_CHECKING, TypeAlias

try:  # pragma: no cover - optional dependency
    import asyncpg  # type: ignore
except Exception:  # pragma: no cover - optional dependency may be absent
    asyncpg = None  # type: ignore

if TYPE_CHECKING:
    from asyncpg.pool import Pool as _AsyncpgPool  # type: ignore
elif asyncpg is not None:
    from asyncpg.pool import Pool as _AsyncpgPool  # type: ignore[attr-defined]
else:  # pragma: no cover - guard when asyncpg unavailable
    _AsyncpgPool = Any  # type: ignore[misc]

PoolType: TypeAlias = _AsyncpgPool

_POOL_REGISTRY: Dict[Tuple[str, int, int], PoolType] = {}
_POOL_LOCK = asyncio.Lock()


async def get_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> PoolType:
    if asyncpg is None:  # pragma: no cover - ensure dependency installed
        raise RuntimeError(
            "asyncpg is required for PostgreSQL operations; install econ-sim[postgres]."
        )

    key = (dsn, min_size, max_size)
    pool = _POOL_REGISTRY.get(key)
    if pool is not None:
        return pool

    async with _POOL_LOCK:
        pool = _POOL_REGISTRY.get(key)
        if pool is not None:
            return pool
        pool = await asyncpg.create_pool(  # type: ignore[attr-defined]
            dsn=dsn,
            min_size=min_size,
            max_size=max_size,
        )
        _POOL_REGISTRY[key] = pool
        return pool


async def close_all_pools() -> None:
    async with _POOL_LOCK:
        pools = list(_POOL_REGISTRY.values())
        _POOL_REGISTRY.clear()
    for pool in pools:
        try:
            await pool.close()
        except Exception:  # pragma: no cover - best effort cleanup
            continue

    async def close_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> None:
        """Close and remove a specific pool identified by (dsn, min_size, max_size).

        This is used by higher-level stores to deterministically close resources
        associated with a particular configuration.
        """
        key = (dsn, min_size, max_size)
        pool = None
        async with _POOL_LOCK:
            pool = _POOL_REGISTRY.pop(key, None)
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                # best-effort
                pass

        async def run_with_retry(
            pool: PoolType,
            func,  # callable that accepts a connection and performs DB work: async def f(conn): ...
            *,
            retries: int = 3,
            base_backoff: float = 0.05,
        ) -> object:
            """Run a DB callable with a transaction and retry on serialization/deadlock errors.

            The callable receives a single acquired connection and should perform its
            work (preferably within an explicit transaction). This helper will retry
            on common transient Postgres errors (serialization failure / deadlock).

            Returns the callable's return value or raises the last exception.
            """
            if asyncpg is None:  # pragma: no cover - optional dependency
                # Fallback: just run once without retry if asyncpg missing (tests may
                # stub out asyncpg in some environments).
                async with pool.acquire() as conn:
                    return await func(conn)

            # common Postgres error codes for retry-worthy failures
            RETRY_CODES = {"40001", "40P01"}  # serialization_failure, deadlock_detected

            last_exc: Exception | None = None
            for attempt in range(1, retries + 1):
                async with pool.acquire() as conn:
                    try:
                        # Ensure caller uses a transaction; if not, caller's work still
                        # happens on the connection but we can't guarantee atomicity.
                        result = await func(conn)
                        return result
                    except Exception as exc:  # pragma: no cover - runtime handling
                        last_exc = exc
                        # If it's an asyncpg.PostgresError with a retryable SQLSTATE,
                        # attempt backoff and retry. Otherwise re-raise.
                        pgcode = getattr(exc, "sqlstate", None)
                        if isinstance(exc, Exception) and pgcode in RETRY_CODES:
                            if attempt == retries:
                                raise
                            backoff = base_backoff * (2 ** (attempt - 1))
                            await asyncio.sleep(backoff)
                            continue
                        raise
            # If we exhausted retries and didn't return, raise the last exception
            if last_exc is not None:
                raise last_exc
            return None
