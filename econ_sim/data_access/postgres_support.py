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
