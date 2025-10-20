"""共享的 PostgreSQL 连接池辅助工具。

此模块维护一个按 (dsn, min_size, max_size) 键入的全局池注册表，以便在多处
需要数据库连接时复用 pool 对象，避免为每个 DataAccessLayer 或 orchestrator
反复创建连接池。
"""

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
    if asyncpg is None:  # pragma: no cover - 运行时可能未安装 asyncpg
        raise RuntimeError(
            "PostgreSQL 操作需要 asyncpg；请安装 econ-sim[postgres] 或单独安装 asyncpg。"
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
    """关闭并移除由 (dsn, min_size, max_size) 标识的特定连接池。

    高层存储组件可调用此函数以确定性地关闭与特定配置相关联的资源。
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
    """在数据库操作上提供重试机制（针对序列化失败/死锁等可重试错误）。

    调用方传入一个接收连接并执行数据库工作的协程函数（建议在显式事务内执行）。
    本助手函数在遇到常见的短暂性 Postgres 错误（如序列化失败或死锁）时会进行重试。

    成功时返回被调用函数的返回值；重试耗尽后抛出最后一次异常。
    """
    if asyncpg is None:  # pragma: no cover - 可选依赖缺失时的回退
        # 回退：当 asyncpg 未安装时仅尝试一次（测试环境可能会替换或模拟 asyncpg）。
        async with pool.acquire() as conn:
            return await func(conn)

    # 常见的可重试 Postgres 错误码（序列化失败 / 死锁检测）
    RETRY_CODES = {"40001", "40P01"}  # serialization_failure, deadlock_detected

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        async with pool.acquire() as conn:
            try:
                # 确保调用方在事务中执行；若未使用事务，调用方的操作仍会在该连接上执行，
                # 但我们无法保证原子性。
                result = await func(conn)
                return result
            except Exception as exc:  # pragma: no cover - runtime handling
                last_exc = exc
                # 若捕获到 asyncpg.PostgresError 且 SQLSTATE 属于可重试错误，
                # 则进行指数回退并重试；否则重新抛出异常。
                pgcode = getattr(exc, "sqlstate", None)
                if isinstance(exc, Exception) and pgcode in RETRY_CODES:
                    if attempt == retries:
                        raise
                    backoff = base_backoff * (2 ** (attempt - 1))
                    await asyncio.sleep(backoff)
                    continue
                raise
    # 若重试耗尽仍未成功，则抛出最后一次捕获的异常
    if last_exc is not None:
        raise last_exc
    return None
