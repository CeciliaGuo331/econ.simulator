"""异步数据访问层及其缓存/持久化存储实现。"""

from __future__ import annotations

import asyncio
import json
import copy
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, Set, TYPE_CHECKING
from collections import deque
import time

try:  # pragma: no cover - optional dependency at runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - fallback when redis isn't installed yet
    Redis = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import asyncpg  # type: ignore
except Exception:  # pragma: no cover - optional dependency may be absent
    asyncpg = None  # type: ignore

if TYPE_CHECKING:  # pragma: no cover - typing hints only
    from asyncpg.pool import Pool as AsyncpgPool  # type: ignore
    from ..script_engine.registry import ScriptFailureEvent
else:  # pragma: no cover - runtime fallback when asyncpg is unavailable
    AsyncpgPool = Any  # type: ignore

from .models import (
    AgentKind,
    HouseholdState,
    LedgerEntry,
    MarketRuntime,
    ScriptFailureRecord,
    SimulationFeatures,
    StateUpdateCommand,
    TradeRecord,
    TickLogEntry,
    TickResult,
    WorldState,
)
from ..utils.settings import WorldConfig, get_world_config
from ..core.entity_factory import (
    create_bank_state,
    create_central_bank_state,
    create_firm_state,
    create_government_state,
    create_household_state,
    create_macro_state,
    create_simulation_features,
)
from .postgres_support import get_pool, run_with_retry
from .postgres_failures import PostgresScriptFailureStore
from .postgres_participants import PostgresParticipantStore
from .postgres_ticklogs import PostgresTickLogStore
from .postgres_utils import quote_identifier


logger = logging.getLogger(__name__)


class SimulationNotFoundError(RuntimeError):
    """当访问不存在的仿真实例时抛出的异常。"""


class StateStore(Protocol):
    """通用状态存储接口，抽象出加载与保存操作。"""

    async def load(self, simulation_id: str) -> Optional[Dict]:
        """根据仿真 ID 异步读取状态快照，若不存在则返回 ``None``。"""

    async def store(self, simulation_id: str, payload: Dict) -> None:
        """将状态快照持久化到存储介质。"""

    async def delete(self, simulation_id: str) -> None:
        """删除指定仿真实例的状态快照。"""


class ScriptFailureStore(Protocol):
    """抽象脚本失败事件持久化的协议。"""

    async def record_many(self, records: Iterable[ScriptFailureRecord]) -> None: ...

    async def list_recent(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[ScriptFailureRecord]: ...

    async def clear(self) -> None: ...


class InMemoryScriptFailureStore:
    """简易的脚本失败事件内存存储。"""

    def __init__(self, retention: int = 500) -> None:
        self._storage: Dict[str, List[ScriptFailureRecord]] = {}
        self._retention = retention
        self._lock = asyncio.Lock()

    async def record_many(self, records: Iterable[ScriptFailureRecord]) -> None:
        items = list(records)
        if not items:
            return
        async with self._lock:
            for record in items:
                bucket = self._storage.setdefault(record.simulation_id, [])
                bucket.append(record)
                bucket.sort(key=lambda entry: entry.occurred_at)
                if self._retention > 0 and len(bucket) > self._retention:
                    del bucket[: -self._retention]

    async def list_recent(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[ScriptFailureRecord]:
        async with self._lock:
            entries = list(self._storage.get(simulation_id, []))
        entries.sort(key=lambda entry: entry.occurred_at, reverse=True)
        if limit is not None and limit > 0:
            entries = entries[:limit]
        return [entry.model_copy(deep=True) for entry in entries]

    async def clear(self) -> None:
        async with self._lock:
            self._storage.clear()


class InMemoryStateStore:
    """使用内存字典保存数据，主要用于测试或本地运行。"""

    def __init__(self) -> None:
        """初始化线程安全的内存存储结构。"""
        self._storage: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()

    async def load(self, simulation_id: str) -> Optional[Dict]:
        """从内存字典中读取仿真状态并返回深拷贝。"""
        async with self._lock:
            snapshot = self._storage.get(simulation_id)
            # Use deepcopy to avoid the overhead of JSON round-trip while
            # still returning an independent copy.
            return copy.deepcopy(snapshot) if snapshot is not None else None

    async def store_entity(
        self, simulation_id: str, scope: str, entity_id: Optional[str], payload: Dict
    ) -> None:
        """Store a single-entity fragment into the in-memory store.

        This enables per-entity incremental writes for long-running simulations.
        """
        async with self._lock:
            bucket = self._storage.setdefault(simulation_id, {})
            if scope == "household":
                houses = bucket.setdefault("households", {})
                # normalize household id as string
                houses[str(entity_id)] = copy.deepcopy(payload)
            elif scope in {"firm", "bank", "government", "central_bank"}:
                bucket[scope] = copy.deepcopy(payload)
            else:
                # world-level or meta
                meta = bucket.setdefault("_meta", {})
                meta.update(copy.deepcopy(payload))

    async def delete_entity(
        self, simulation_id: str, scope: str, entity_id: Optional[str]
    ) -> None:
        async with self._lock:
            bucket = self._storage.get(simulation_id)
            if not bucket:
                return
            if scope == "household":
                houses = bucket.get("households")
                if houses:
                    houses.pop(str(entity_id), None)
            elif scope in {"firm", "bank", "government", "central_bank"}:
                bucket.pop(scope, None)
            else:
                bucket.pop("_meta", None)

    async def store(self, simulation_id: str, payload: Dict) -> None:
        """将仿真状态深拷贝后写入内存字典。"""
        async with self._lock:
            # Use deepcopy instead of JSON round-trip to improve perf.
            self._storage[simulation_id] = copy.deepcopy(payload)

    async def delete(self, simulation_id: str) -> None:
        """从内存存储中移除指定仿真实例的状态。"""
        async with self._lock:
            self._storage.pop(simulation_id, None)


class RedisStateStore:
    """基于 Redis 的 JSON 存储，实现跨进程持久化。"""

    def __init__(self, redis: Redis, prefix: str = "econ_sim") -> None:  # type: ignore[valid-type]
        """注入 Redis 客户端及键前缀，构造存储实例。"""
        if redis is None:  # pragma: no cover - defensive guard when redis import failed
            raise RuntimeError(
                "Redis client is not available; ensure redis-py is installed."
            )
        self._redis = redis
        self._prefix = prefix

    def _key(self, simulation_id: str) -> str:
        """按照统一前缀拼接 Redis 键名称。"""
        return f"{self._prefix}:sim:{simulation_id}:world_state"

    def _household_key(self, simulation_id: str, household_id: str) -> str:
        return f"{self._prefix}:sim:{simulation_id}:household:{household_id}"

    def _entity_key(self, simulation_id: str, scope: str) -> str:
        return f"{self._prefix}:sim:{simulation_id}:entity:{scope}"

    async def load(self, simulation_id: str) -> Optional[Dict]:
        """从 Redis 获取指定仿真状态的 JSON 快照。"""
        data = await self._redis.get(self._key(simulation_id))
        if data is None:
            return None
        return json.loads(data)

    async def store(self, simulation_id: str, payload: Dict) -> None:
        """将仿真状态序列化为 JSON 并写入 Redis。"""
        await self._redis.set(self._key(simulation_id), json.dumps(payload))

    async def delete(self, simulation_id: str) -> None:
        """从 Redis 中删除指定仿真实例的状态。"""
        await self._redis.delete(self._key(simulation_id))


class RedisRuntimeStore:
    """交易撮合与账户流水的运行时数据结构（Redis 承载）。"""

    def __init__(self, redis: Redis, prefix: str = "econ_sim") -> None:  # type: ignore[valid-type]
        if redis is None:  # pragma: no cover
            raise RuntimeError("Redis client is not available")
        self._redis = redis
        self._prefix = prefix

    def _runtime_key(self, simulation_id: str) -> str:
        return f"{self._prefix}:sim:{simulation_id}:market_runtime"

    def _trades_key(self, simulation_id: str) -> str:
        return f"{self._prefix}:sim:{simulation_id}:trades"

    def _ledger_key(self, simulation_id: str) -> str:
        return f"{self._prefix}:sim:{simulation_id}:ledger"

    async def get_runtime(self, simulation_id: str) -> MarketRuntime:
        raw = await self._redis.get(self._runtime_key(simulation_id))
        if not raw:
            return MarketRuntime()
        data = json.loads(raw)
        return MarketRuntime.model_validate(data)

    async def set_runtime(self, simulation_id: str, runtime: MarketRuntime) -> None:
        await self._redis.set(
            self._runtime_key(simulation_id), runtime.model_dump_json()
        )

    async def append_trades(
        self, simulation_id: str, trades: Iterable[TradeRecord]
    ) -> int:
        items = [t.model_dump_json() for t in trades]
        if not items:
            return 0
        return await self._redis.rpush(self._trades_key(simulation_id), *items)

    async def list_trades(
        self, simulation_id: str, start: int = -200, end: int = -1
    ) -> List[TradeRecord]:
        values = await self._redis.lrange(self._trades_key(simulation_id), start, end)
        out: List[TradeRecord] = []
        for v in values:
            try:
                data = json.loads(v)
                out.append(TradeRecord.model_validate(data))
            except Exception:
                continue
        return out

    async def append_ledger(
        self, simulation_id: str, entries: Iterable[LedgerEntry], *, max_len: int = 5000
    ) -> int:
        items = [e.model_dump_json() for e in entries]
        if not items:
            return 0
        key = self._ledger_key(simulation_id)
        # 追加并限制长度
        n = await self._redis.rpush(key, *items)
        await self._redis.ltrim(key, -max_len, -1)
        return n

    async def list_ledger(
        self, simulation_id: str, start: int = -500, end: int = -1
    ) -> List[LedgerEntry]:
        values = await self._redis.lrange(self._ledger_key(simulation_id), start, end)
        out: List[LedgerEntry] = []
        for v in values:
            try:
                data = json.loads(v)
                out.append(LedgerEntry.model_validate(data))
            except Exception:
                continue
        return out


class PersistenceError(RuntimeError):
    """在持久化流程出现不可恢复错误时抛出的异常。"""


class CompositeStateStore(StateStore):
    """组合存储实现，提供 Redis 缓存 + PostgreSQL 持久化 + 内存兜底。"""

    def __init__(
        self,
        *,
        cache: Optional[StateStore] = None,
        persistent: Optional[StateStore] = None,
        fallback: Optional[StateStore] = None,
    ) -> None:
        if cache is None and persistent is None and fallback is None:
            raise ValueError("CompositeStateStore requires at least one backing store")
        self._cache = cache
        self._persistent = persistent
        self._fallback = fallback

    async def load(self, simulation_id: str) -> Optional[Dict]:
        """优先从缓存读取，未命中时回源并自动回填。"""

        if self._cache is not None:
            try:
                cached = await self._cache.load(simulation_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to load simulation %s from cache",
                    simulation_id,
                    exc_info=exc,
                )
            else:
                if cached is not None:
                    return cached

        payload: Optional[Dict] = None

        if self._persistent is not None:
            try:
                payload = await self._persistent.load(simulation_id)
            except Exception as exc:  # pragma: no cover - allow fallback
                logger.error(
                    "Failed to load simulation %s from persistent store",
                    simulation_id,
                    exc_info=exc,
                )
            else:
                if payload is not None:
                    if self._cache is not None:
                        try:
                            await self._cache.store(simulation_id, payload)
                        except (
                            Exception
                        ) as exc:  # pragma: no cover - cache warming failure is non-fatal
                            logger.warning(
                                "Failed to warm cache for simulation %s",
                                simulation_id,
                                exc_info=exc,
                            )
                    if self._fallback is not None:
                        try:
                            await self._fallback.store(simulation_id, payload)
                        except (
                            Exception
                        ) as exc:  # pragma: no cover - fallback warming warning
                            logger.warning(
                                "Failed to warm fallback store for simulation %s",
                                simulation_id,
                                exc_info=exc,
                            )
                    return payload

        if payload is None and self._fallback is not None:
            try:
                payload = await self._fallback.load(simulation_id)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.error(
                    "Failed to load simulation %s from fallback store",
                    simulation_id,
                    exc_info=exc,
                )
            else:
                if payload is not None and self._cache is not None:
                    try:
                        await self._cache.store(simulation_id, payload)
                    except Exception as exc:  # pragma: no cover
                        logger.warning(
                            "Failed to warm cache for simulation %s",
                            simulation_id,
                            exc_info=exc,
                        )
                return payload

        return payload

    async def store(self, simulation_id: str, payload: Dict) -> None:
        """写穿缓存与持久层，确保持久化成功。"""
        # First, persist to primary persistent store if configured. This
        # prevents updating the cache before the authoritative copy is saved.
        if self._persistent is not None:
            try:
                if hasattr(self._persistent, "store_entity"):
                    houses = payload.get("households", {})
                    for hid, hpayload in houses.items():
                        await self._persistent.store_entity(
                            simulation_id, "household", str(hid), hpayload
                        )
                    for scope in (
                        "firm",
                        "bank",
                        "government",
                        "central_bank",
                        "macro",
                        "features",
                        "household_shocks",
                    ):
                        if scope in payload:
                            await self._persistent.store_entity(
                                simulation_id, scope, None, payload[scope]
                            )
                else:
                    await self._persistent.store(simulation_id, payload)
            except Exception as exc:
                # If primary persistent store write fails, attempt best-effort
                # write to fallback (if configured) and raise a PersistenceError
                if self._fallback is not None:
                    try:
                        await self._fallback.store(simulation_id, payload)
                    except Exception:
                        logger.exception(
                            "Persistent store failed and fallback write also failed for %s",
                            simulation_id,
                        )
                raise PersistenceError(
                    "Failed to persist world state to primary store"
                ) from exc
        else:
            # No persistent store configured: fall back to fallback/store order
            if self._fallback is not None:
                try:
                    await self._fallback.store(simulation_id, payload)
                except Exception as exc:
                    raise PersistenceError(
                        "Failed to persist to fallback store"
                    ) from exc
            elif self._cache is None:
                raise PersistenceError(
                    "No persistent store configured for simulation state"
                )

        # Primary persistence succeeded (or fallback used when no primary).
        # Now update cache (best-effort). Cache failures should not cause the
        # authoritative persistent copy to be discarded.
        if self._cache is not None:
            try:
                if hasattr(self._cache, "store_entity"):
                    houses = payload.get("households", {})
                    for hid, hpayload in houses.items():
                        await self._cache.store_entity(
                            simulation_id, "household", str(hid), hpayload
                        )
                    for scope in (
                        "firm",
                        "bank",
                        "government",
                        "central_bank",
                        "macro",
                        "features",
                        "household_shocks",
                    ):
                        if scope in payload:
                            await self._cache.store_entity(
                                simulation_id, scope, None, payload[scope]
                            )
                else:
                    await self._cache.store(simulation_id, payload)
            except (
                Exception
            ) as exc:  # pragma: no cover - cache update failure is non-fatal
                logger.warning(
                    "Failed to update cache for simulation %s",
                    simulation_id,
                    exc_info=exc,
                )

        # Finally, update fallback store for warming if both primary and
        # fallback are configured (best-effort).
        if self._fallback is not None and self._persistent is not None:
            try:
                await self._fallback.store(simulation_id, payload)
            except Exception as exc:  # pragma: no cover - fallback warming warning
                logger.warning(
                    "Failed to update fallback store for simulation %s",
                    simulation_id,
                    exc_info=exc,
                )

    async def delete(self, simulation_id: str) -> None:
        """同时在缓存与持久层删除指定仿真。"""

        cache_error: Optional[Exception] = None
        if self._cache is not None:
            try:
                await self._cache.delete(simulation_id)
            except Exception as exc:  # pragma: no cover - cache delete best effort
                cache_error = exc
                logger.warning(
                    "Failed to delete simulation %s from cache",
                    simulation_id,
                    exc_info=exc,
                )

        if self._persistent is not None:
            try:
                await self._persistent.delete(simulation_id)
            except Exception as exc:
                raise PersistenceError(
                    "Failed to delete simulation from persistent store"
                ) from exc
        elif self._fallback is not None:
            await self._fallback.delete(simulation_id)

        if self._fallback is not None and self._persistent is not None:
            try:
                await self._fallback.delete(simulation_id)
            except Exception as exc:  # pragma: no cover - fallback cleanup warning
                logger.warning(
                    "Failed to delete simulation %s from fallback store",
                    simulation_id,
                    exc_info=exc,
                )

        if cache_error is not None and self._persistent is None:
            raise PersistenceError(
                "Cache delete failed without persistent backup"
            ) from cache_error


class PostgresStateStore(StateStore):
    """基于 PostgreSQL 的 JSONB 世界状态持久层。"""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        table: str = "world_state_snapshots",
        min_pool_size: int = 1,
        max_pool_size: int = 5,
        create_schema: bool = True,
    ) -> None:
        if asyncpg is None:  # pragma: no cover - optional dependency guard
            raise RuntimeError(
                "asyncpg is required for PostgresStateStore; install econ-sim[postgres] or add asyncpg."
            )

        self._dsn = dsn
        self._schema = schema
        self._table = table
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._create_schema = create_schema
        self._pool: Optional[AsyncpgPool] = None
        self._qualified_table: Optional[str] = None
        self._pool_lock = asyncio.Lock()
        self._qualified_entities_table = None
        # configurable batch size for bulk upserts (number of entities per INSERT)
        self._upsert_batch_size = int(os.getenv("ECON_SIM_PG_UPSERT_BATCH", "500"))

    async def _get_pool(self) -> AsyncpgPool:
        if self._pool is not None:
            return self._pool

        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            pool = await get_pool(
                self._dsn,
                min_size=self._min_pool_size,
                max_size=self._max_pool_size,
            )
            await self._ensure_schema(pool)
            self._pool = pool
        return self._pool

    async def _ensure_schema(self, pool: AsyncpgPool) -> None:
        schema_ident = quote_identifier(self._schema)
        table_ident = quote_identifier(self._table)
        qualified_table = f"{schema_ident}.{table_ident}"
        index_ident = quote_identifier(f"{self._table}_updated_at_idx")
        entities_table_ident = quote_identifier(f"{self._table}_entities")
        qualified_entities = f"{schema_ident}.{entities_table_ident}"

        async with pool.acquire() as conn:
            if self._create_schema:
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ident}")
            # Create a compact snapshot table for full world snapshots. Per-entity
            # incremental persistence uses a separate entities table created below.
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {qualified_table} (
                    simulation_id TEXT PRIMARY KEY,
                    tick INTEGER NOT NULL,
                    day INTEGER NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
                )
                """
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_ident} ON {qualified_table} (updated_at DESC)"
            )
            # entities table for incremental persistence (per-scope, per-entity rows)
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {qualified_entities} (
                    simulation_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
                    PRIMARY KEY (simulation_id, scope, entity_id)
                )
                """
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {quote_identifier(self._table + '_entities_updated_at_idx')} ON {qualified_entities} (updated_at DESC)"
            )

            self._qualified_table = qualified_table
            # store qualified entities table name on instance
            self._qualified_entities_table = qualified_entities

    async def load(self, simulation_id: str) -> Optional[Dict]:
        pool = await self._get_pool()
        if self._qualified_table is None or self._qualified_entities_table is None:
            raise RuntimeError("PostgresStateStore schema not initialized")

        async with pool.acquire() as conn:
            # Prefer incremental entities table when it contains data for this simulation
            rows = await conn.fetch(
                f"SELECT scope, entity_id, payload FROM {self._qualified_entities_table} WHERE simulation_id = $1",
                simulation_id,
            )
            if rows:
                result: Dict[str, Any] = {}
                # collect households specially
                households: Dict[str, Any] = {}
                for r in rows:
                    scope = r["scope"]
                    eid = r["entity_id"]
                    payload = r["payload"]
                    if scope == "household":
                        households[eid] = payload
                    else:
                        # singletons and meta
                        result[scope] = payload
                if households:
                    result["households"] = households
                return result

            # fallback to snapshot table
            row = await conn.fetchrow(
                f"SELECT payload FROM {self._qualified_table} WHERE simulation_id = $1",
                simulation_id,
            )
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            return json.loads(payload)
        return dict(payload)

    async def store(self, simulation_id: str, payload: Dict) -> None:
        pool = await self._get_pool()
        if self._qualified_entities_table is None:  # pragma: no cover - defensive guard
            raise RuntimeError("PostgresStateStore schema not initialized")

        # decompose payload into entities and upsert into entities table
        houses = payload.get("households", {})
        tick = int(payload.get("tick", 0))
        day = int(payload.get("day", 0))
        # prepare rows outside of connection to keep logic simple
        rows: List[tuple] = []
        for hid, hpayload in houses.items():
            rows.append((simulation_id, "household", str(hid), json.dumps(hpayload)))

        for scope in (
            "firm",
            "bank",
            "government",
            "central_bank",
            "macro",
            "features",
            "household_shocks",
        ):
            if scope in payload:
                rows.append((simulation_id, scope, "", json.dumps(payload[scope])))

        if not rows:
            return

        batch_size = max(1, int(getattr(self, "_upsert_batch_size", 500)))

        async def _do_upsert(conn):
            # perform all upserts within a single transaction to ensure atomic
            # commit per-tick and avoid partial state persistence
            async with conn.transaction():
                # execute batches of multi-row INSERT ... ON CONFLICT statements
                for i in range(0, len(rows), batch_size):
                    batch = rows[i : i + batch_size]
                    # build parameterized VALUES clause
                    params: List[Any] = []
                    values_fragments: List[str] = []
                    # each row uses 4 params: simulation_id, scope, entity_id, payload (payload cast to jsonb)
                    param_index = 1
                    for r in batch:
                        params.extend([r[0], r[1], r[2], r[3]])
                        placeholders = f"${param_index}, ${param_index+1}, ${param_index+2}, ${param_index+3}::jsonb, timezone('utc', now())"
                        values_fragments.append(f"({placeholders})")
                        param_index += 4

                    values_sql = ",\n".join(values_fragments)
                    sql = f"""
                        INSERT INTO {self._qualified_entities_table} (simulation_id, scope, entity_id, payload, updated_at)
                        VALUES
                        {values_sql}
                        ON CONFLICT (simulation_id, scope, entity_id)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                    """
                    await conn.execute(sql, *params)

        pool = await self._get_pool()
        # Use retry wrapper to handle transient serialization/deadlock errors
        await run_with_retry(pool, _do_upsert)

    async def list_simulation_ids(self) -> list[str]:
        pool = await self._get_pool()
        if self._qualified_table is None:  # pragma: no cover - defensive guard
            raise RuntimeError("PostgresStateStore schema not initialized")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT simulation_id FROM {self._qualified_table} ORDER BY simulation_id"
            )
        return [row["simulation_id"] for row in rows]

    async def delete(self, simulation_id: str) -> None:
        pool = await self._get_pool()
        if self._qualified_table is None:  # pragma: no cover - defensive guard
            raise RuntimeError("PostgresStateStore schema not initialized")
        async with pool.acquire() as conn:
            await conn.execute(
                f"DELETE FROM {self._qualified_table} WHERE simulation_id = $1",
                simulation_id,
            )

    async def close(self) -> None:  # pragma: no cover - retained for compatibility
        self._pool = None

    async def store_entity(
        self, simulation_id: str, scope: str, entity_id: Optional[str], payload: Dict
    ) -> None:
        pool = await self._get_pool()
        if self._qualified_entities_table is None:
            raise RuntimeError("PostgresStateStore schema not initialized")
        data = json.dumps(payload)
        eid = "" if entity_id is None else str(entity_id)

        async def _do_store_entity(conn):
            await conn.execute(
                f"""
                INSERT INTO {self._qualified_entities_table} (simulation_id, scope, entity_id, payload, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, timezone('utc', now()))
                ON CONFLICT (simulation_id, scope, entity_id)
                DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                """,
                simulation_id,
                scope,
                eid,
                data,
            )

        pool = await self._get_pool()
        await run_with_retry(pool, _do_store_entity)

    async def delete_entity(
        self, simulation_id: str, scope: str, entity_id: Optional[str]
    ) -> None:
        pool = await self._get_pool()
        if self._qualified_entities_table is None:
            raise RuntimeError("PostgresStateStore schema not initialized")
        eid = "" if entity_id is None else str(entity_id)
        async with pool.acquire() as conn:
            await conn.execute(
                f"DELETE FROM {self._qualified_entities_table} WHERE simulation_id = $1 AND scope = $2 AND entity_id = $3",
                simulation_id,
                scope,
                eid,
            )


@dataclass
class DataAccessLayer:
    """为调度器提供统一入口的数据访问外观类。"""

    config: WorldConfig
    store: StateStore
    cache_store: Optional[StateStore] = None
    persistent_store: Optional[StateStore] = None
    fallback_store: Optional[StateStore] = None
    participant_store: Optional[PostgresParticipantStore] = None
    failure_store: ScriptFailureStore = field(
        default_factory=InMemoryScriptFailureStore
    )
    _participants: Dict[str, Set[str]] = field(default_factory=dict)
    _known_simulations: Set[str] = field(default_factory=set)
    _hydrated_simulations: bool = field(default=False, init=False, repr=False)
    _tick_logs: Dict[str, List[TickLogEntry]] = field(default_factory=dict)
    _log_retention: int = field(default=1000)
    _tick_log_store: Optional[PostgresTickLogStore] = None
    _runtime_store: Optional[RedisRuntimeStore] = None
    # per-simulation write locks to prevent concurrent writes from clobbering each other
    _write_locks: Dict[str, asyncio.Lock] = field(default_factory=dict)
    # background sampler task and storage
    _sampler_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    _recent_sampler: deque = field(
        default_factory=lambda: deque(maxlen=200), init=False, repr=False
    )
    _sampler_interval: float = field(default=5.0)

    @classmethod
    def with_default_store(
        cls, config: Optional[WorldConfig] = None
    ) -> "DataAccessLayer":
        """根据环境变量构建数据访问层，默认回退到纯内存实现。"""

        resolved_config = config or get_world_config()
        fallback = InMemoryStateStore()

        redis_url = os.getenv("ECON_SIM_REDIS_URL")
        redis_prefix = os.getenv("ECON_SIM_REDIS_PREFIX", "econ_sim")
        postgres_dsn = os.getenv("ECON_SIM_POSTGRES_DSN")
        pg_schema = os.getenv("ECON_SIM_POSTGRES_SCHEMA", "public")
        pg_table = os.getenv("ECON_SIM_POSTGRES_TABLE", "world_state_snapshots")
        pg_min_pool = int(os.getenv("ECON_SIM_POSTGRES_MIN_POOL", "1"))
        pg_max_pool = int(os.getenv("ECON_SIM_POSTGRES_MAX_POOL", "5"))

        cache_store: Optional[StateStore] = None
        persistent_store: Optional[StateStore] = None
        participant_store: Optional[PostgresParticipantStore] = None
        failure_store: ScriptFailureStore = InMemoryScriptFailureStore()
        tick_log_store: Optional[PostgresTickLogStore] = None

        if redis_url and Redis is not None:
            redis_client = Redis.from_url(
                redis_url, encoding="utf-8", decode_responses=False
            )
            cache_store = RedisStateStore(redis_client, prefix=redis_prefix)
            runtime_store = RedisRuntimeStore(redis_client, prefix=redis_prefix)
        else:
            runtime_store = None

        if postgres_dsn:
            persistent_store = PostgresStateStore(
                postgres_dsn,
                schema=pg_schema,
                table=pg_table,
                min_pool_size=pg_min_pool,
                max_pool_size=pg_max_pool,
            )
            participant_store = PostgresParticipantStore(
                postgres_dsn,
                schema=pg_schema,
                min_pool_size=pg_min_pool,
                max_pool_size=pg_max_pool,
            )
            failure_store = PostgresScriptFailureStore(
                postgres_dsn,
                schema=pg_schema,
                min_pool_size=pg_min_pool,
                max_pool_size=pg_max_pool,
            )
            tick_log_store = PostgresTickLogStore(
                postgres_dsn,
                schema=pg_schema,
                min_pool_size=pg_min_pool,
                max_pool_size=pg_max_pool,
            )

        if cache_store or persistent_store:
            composite = CompositeStateStore(
                cache=cache_store,
                persistent=persistent_store,
                fallback=fallback,
            )
            instance = cls(
                config=resolved_config,
                store=composite,
                cache_store=cache_store,
                persistent_store=persistent_store,
                fallback_store=fallback,
                participant_store=participant_store,
                failure_store=failure_store,
                _tick_log_store=tick_log_store,
                _runtime_store=runtime_store,
            )
            # sampler will be started explicitly via start_sampler() when desired
            return instance

        # fallback to pure in-memory store when no cache or persistent configured
        instance = cls(
            config=resolved_config,
            store=fallback,
            fallback_store=fallback,
            participant_store=participant_store,
            failure_store=failure_store,
            _runtime_store=None,
        )
        return instance

    def start_sampler(self) -> None:
        """Start the background sampler if not already running and an event loop is available."""
        if self._sampler_task is not None and not self._sampler_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
            self._sampler_task = loop.create_task(self._background_sampler())
        except RuntimeError:
            # no running loop; caller may start sampler later
            self._sampler_task = None

    def stop_sampler(self) -> None:
        """Stop the background sampler if running."""
        if self._sampler_task is not None:
            try:
                self._sampler_task.cancel()
            except Exception:
                pass
            self._sampler_task = None

    async def _background_sampler(self) -> None:
        """Periodically sample simple runtime stats for monitoring (non-critical)."""
        try:
            while True:
                try:
                    sample = {
                        "timestamp": time.time(),
                        "write_locks": len(self._write_locks),
                        "known_simulations": len(self._known_simulations),
                        "tick_logs_backlog": sum(
                            len(v) for v in self._tick_logs.values()
                        ),
                    }
                except Exception:
                    sample = {"timestamp": time.time(), "error": True}
                self._recent_sampler.append(sample)
                await asyncio.sleep(self._sampler_interval)
        except asyncio.CancelledError:
            return

    def get_runtime_samples(self) -> list:
        """Return recent sampler values as a list (newest last)."""
        return list(self._recent_sampler)

    async def ensure_simulation(self, simulation_id: str) -> WorldState:
        """确保仿真实例存在，不存在时按配置创建初始世界状态。"""
        existing = await self.store.load(simulation_id)
        if existing is not None:
            world_state = WorldState.model_validate(existing)
            self._known_simulations.add(simulation_id)
            self._tick_logs.setdefault(simulation_id, [])
            return world_state

        world_state = self._build_initial_world_state(simulation_id)
        await self._persist_state(world_state)
        self._tick_logs[simulation_id] = []
        return world_state

    async def reset_simulation(self, simulation_id: str) -> WorldState:
        """无论当前状态如何，重新生成初始世界状态并覆盖存储。"""

        world_state = self._build_initial_world_state(simulation_id)
        await self._persist_state(world_state)
        self._tick_logs[simulation_id] = []
        return world_state

    async def delete_simulation(self, simulation_id: str) -> int:
        """彻底移除指定仿真实例的世界状态，并返回解除关联的参与者数量。"""

        existing = await self.store.load(simulation_id)
        if existing is None:
            raise SimulationNotFoundError(f"Simulation '{simulation_id}' not found")

        await self.store.delete(simulation_id)
        participants = self._participants.pop(simulation_id, set())
        removed_count = len(participants)
        if self.participant_store is not None:
            removed = await self.participant_store.remove_simulation(simulation_id)
            removed_count = max(removed_count, removed)
        self._known_simulations.discard(simulation_id)
        self._tick_logs.pop(simulation_id, None)
        return removed_count

    async def get_world_state(self, simulation_id: str) -> WorldState:
        """读取指定仿真实例的最新世界状态。"""
        payload = await self.store.load(simulation_id)
        if payload is None:
            raise SimulationNotFoundError(f"Simulation '{simulation_id}' not found")
        world_state = WorldState.model_validate(payload)
        self._known_simulations.add(simulation_id)
        return world_state

    async def apply_updates(
        self, simulation_id: str, updates: list[StateUpdateCommand]
    ) -> WorldState:
        """根据状态更新指令列表逐条修改世界状态并持久化。"""
        state = await self.get_world_state(simulation_id)
        mutable = state.model_dump()

        for update in updates:
            self._apply_single_update(mutable, update)

        updated_state = WorldState.model_validate(mutable)
        await self._persist_state(updated_state)
        return updated_state

    async def ensure_entity_state(
        self,
        simulation_id: str,
        agent_kind: AgentKind,
        entity_id: str,
    ) -> WorldState:
        """确保指定主体实体存在，若缺失则按默认模板创建。"""

        state = await self.get_world_state(simulation_id)
        mutated = state.model_copy(deep=True)
        changed = False

        if agent_kind is AgentKind.HOUSEHOLD:
            try:
                household_id = int(entity_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Household entity_id must be an integer") from exc
            if household_id not in mutated.households:
                mutated.households[household_id] = create_household_state(
                    self.config, household_id
                )
                mutated.household_shocks.pop(household_id, None)
                changed = True
        elif agent_kind is AgentKind.FIRM:
            if mutated.firm is None or mutated.firm.id != entity_id:
                mutated.firm = create_firm_state(self.config, entity_id)
                changed = True
        elif agent_kind is AgentKind.BANK:
            if mutated.bank is None or mutated.bank.id != entity_id:
                mutated.bank = create_bank_state(
                    self.config, entity_id, mutated.households
                )
                changed = True
        elif agent_kind is AgentKind.GOVERNMENT:
            if mutated.government is None or mutated.government.id != entity_id:
                mutated.government = create_government_state(self.config, entity_id)
                changed = True
        elif agent_kind is AgentKind.CENTRAL_BANK:
            if mutated.central_bank is None or mutated.central_bank.id != entity_id:
                mutated.central_bank = create_central_bank_state(self.config, entity_id)
                changed = True
        else:
            raise ValueError(f"Unsupported agent kind for seeding: {agent_kind}")

        if changed:
            await self._persist_state(mutated)
            return mutated
        return state

    async def remove_entity_state(
        self,
        simulation_id: str,
        agent_kind: AgentKind,
        entity_id: str,
    ) -> WorldState:
        """移除指定主体实体，若不存在则无操作。"""

        state = await self.get_world_state(simulation_id)
        mutated = state.model_copy(deep=True)
        changed = False

        if agent_kind is AgentKind.HOUSEHOLD:
            identifiers = {entity_id}
            try:
                identifiers.add(int(entity_id))
            except (TypeError, ValueError):
                pass
            for identifier in list(identifiers):
                if isinstance(identifier, str):
                    try:
                        identifier = int(identifier)
                    except ValueError:
                        continue
                if identifier in mutated.households:
                    mutated.households.pop(identifier, None)
                    mutated.household_shocks.pop(identifier, None)
                    changed = True
        elif agent_kind is AgentKind.FIRM:
            if mutated.firm is not None and mutated.firm.id == entity_id:
                mutated.firm = None
                changed = True
        elif agent_kind is AgentKind.BANK:
            if mutated.bank is not None and mutated.bank.id == entity_id:
                mutated.bank = None
                changed = True
        elif agent_kind is AgentKind.GOVERNMENT:
            if mutated.government is not None and mutated.government.id == entity_id:
                mutated.government = None
                changed = True
        elif agent_kind is AgentKind.CENTRAL_BANK:
            if (
                mutated.central_bank is not None
                and mutated.central_bank.id == entity_id
            ):
                mutated.central_bank = None
                changed = True
        else:
            raise ValueError(f"Unsupported agent kind for removal: {agent_kind}")

        if changed:
            await self._persist_state(mutated)
            return mutated
        return state

    async def record_tick(self, tick_result: TickResult) -> None:
        """记录仿真步执行结果，当前仅持久化最新世界状态。"""
        await self._persist_state(tick_result.world_state)
        simulation_id = tick_result.world_state.simulation_id
        if tick_result.logs:
            stored = self._tick_logs.setdefault(simulation_id, [])
            stored.extend(tick_result.logs)
            if len(stored) > self._log_retention:
                stored[:] = stored[-self._log_retention :]
            # Persist to Postgres when available for history queries
            if self._tick_log_store is not None:
                await self._tick_log_store.record_many(simulation_id, tick_result.logs)

    # ---- Market runtime & ledger helpers ----

    async def get_market_runtime(self, simulation_id: str) -> MarketRuntime:
        if self._runtime_store is None:
            return MarketRuntime()
        return await self._runtime_store.get_runtime(simulation_id)

    async def set_market_runtime(
        self, simulation_id: str, runtime: MarketRuntime
    ) -> None:
        if self._runtime_store is None:
            return
        await self._runtime_store.set_runtime(simulation_id, runtime)

    async def append_trades(
        self, simulation_id: str, trades: Iterable[TradeRecord]
    ) -> int:
        if self._runtime_store is None:
            return 0
        return await self._runtime_store.append_trades(simulation_id, trades)

    async def list_recent_trades(
        self, simulation_id: str, limit: int = 200
    ) -> List[TradeRecord]:
        if self._runtime_store is None:
            return []
        return await self._runtime_store.list_trades(
            simulation_id, start=-limit, end=-1
        )

    async def append_ledger(
        self, simulation_id: str, entries: Iterable[LedgerEntry], *, max_len: int = 5000
    ) -> int:
        if self._runtime_store is None:
            return 0
        return await self._runtime_store.append_ledger(
            simulation_id, entries, max_len=max_len
        )

    async def list_recent_ledger(
        self, simulation_id: str, limit: int = 500
    ) -> List[LedgerEntry]:
        if self._runtime_store is None:
            return []
        return await self._runtime_store.list_ledger(
            simulation_id, start=-limit, end=-1
        )

    async def register_participant(self, simulation_id: str, user_id: str) -> None:
        """登记参与同一仿真实例的用户，用于共享会话管理。"""

        participants = self._participants.setdefault(simulation_id, set())
        participants.add(user_id)
        if self.participant_store is not None:
            await self.participant_store.register(simulation_id, user_id)

    async def list_participants(self, simulation_id: str) -> list[str]:
        """返回已登记的参与者列表。"""

        participants = self._participants.get(simulation_id)
        if (
            participants is None or not participants
        ) and self.participant_store is not None:
            fetched = await self.participant_store.list_participants(simulation_id)
            participants = set(fetched)
            if participants:
                self._participants[simulation_id] = participants
        return sorted(participants or [])

    async def get_recent_logs(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> list[TickLogEntry]:
        """返回指定仿真实例的最近日志条目。"""

        entries = self._tick_logs.get(simulation_id, [])
        if not entries:
            return []
        if limit is None or limit <= 0:
            window = entries
        else:
            window = entries[-limit:]
        return [TickLogEntry.model_validate(item.model_dump()) for item in window]

    async def query_tick_logs(
        self,
        simulation_id: str,
        *,
        since_tick: Optional[int] = None,
        until_tick: Optional[int] = None,
        since_day: Optional[int] = None,
        until_day: Optional[int] = None,
        message: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[TickLogEntry]:
        """查询历史 Tick 日志（需要 Postgres 持久化支持）。"""
        if self._tick_log_store is None:
            # 回退为空列表，避免在无 Postgres 时抛错
            return []
        return await self._tick_log_store.query(
            simulation_id,
            since_tick=since_tick,
            until_tick=until_tick,
            since_day=since_day,
            until_day=until_day,
            message=message,
            limit=limit,
            offset=offset,
        )

    async def record_script_failures(
        self, events: Iterable["ScriptFailureEvent"]
    ) -> None:
        batch = list(events)
        if not batch:
            return
        records = [
            ScriptFailureRecord(
                failure_id=str(uuid.uuid4()),
                simulation_id=event.simulation_id,
                script_id=event.script_id,
                user_id=event.user_id,
                agent_kind=event.agent_kind,
                entity_id=event.entity_id,
                message=event.message,
                traceback=event.traceback,
                occurred_at=event.occurred_at,
            )
            for event in batch
        ]
        await self.failure_store.record_many(records)

    async def list_script_failures(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[ScriptFailureRecord]:
        return await self.failure_store.list_recent(simulation_id, limit)

    async def list_simulations(self) -> list[str]:
        """返回已知的仿真实例 ID 列表，必要时从持久层回填。"""

        if not self._known_simulations and not self._hydrated_simulations:
            await self._hydrate_simulations()
        return sorted(self._known_simulations)

    async def _hydrate_simulations(self) -> None:
        if self._hydrated_simulations:
            return

        persistent = self.persistent_store
        lister = (
            getattr(persistent, "list_simulation_ids", None) if persistent else None
        )
        if not callable(lister):
            self._hydrated_simulations = True
            return

        try:
            ids = await lister()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to hydrate simulation ids from persistent store",
                exc_info=exc,
            )
            return

        self._known_simulations.update(ids)
        self._hydrated_simulations = True

    async def _persist_state(self, world_state: WorldState) -> None:
        """将世界状态写回底层存储。"""
        sim_id = world_state.simulation_id
        # obtain or create per-simulation lock
        lock = self._write_locks.get(sim_id)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[sim_id] = lock

        async with lock:
            await self.store.store(sim_id, world_state.model_dump())
            # mark known simulation after successful persist
            self._known_simulations.add(sim_id)

    def _build_initial_world_state(self, simulation_id: str) -> WorldState:
        """依据配置构造新的初始世界状态。"""
        sim_cfg = self.config.simulation

        return WorldState(
            simulation_id=simulation_id,
            tick=sim_cfg.initial_tick,
            day=sim_cfg.initial_day,
            households={},
            firm=None,
            bank=None,
            government=None,
            central_bank=None,
            macro=create_macro_state(),
            features=create_simulation_features(self.config),
            household_shocks={},
        )

    def _apply_single_update(
        self, mutable_state: Dict, update: StateUpdateCommand
    ) -> None:
        """在世界状态字典上应用单条更新指令，可处理多种主体作用域。"""
        scope = update.scope
        target_container: Optional[Dict] = None

        resolved_key: Optional[int | str] = None

        if scope is AgentKind.HOUSEHOLD:
            households = mutable_state["households"]
            if update.agent_id is None:
                raise ValueError("Household update requires an agent_id")
            key_candidates = [update.agent_id]
            if not isinstance(update.agent_id, str):
                key_candidates.append(str(update.agent_id))
            else:
                try:
                    key_candidates.append(int(update.agent_id))
                except ValueError:  # pragma: no cover - non-numeric string IDs
                    pass

            resolved_key = None
            for candidate in key_candidates:
                if candidate in households:
                    resolved_key = candidate
                    break

            if resolved_key is None:
                raise KeyError(f"Household {update.agent_id} not found in state")

            target_container = households[resolved_key]
        elif scope is AgentKind.FIRM:
            target_container = mutable_state.get("firm")
            if target_container is None:
                target_container = {}
        elif scope is AgentKind.BANK:
            target_container = mutable_state.get("bank")
            if target_container is None:
                target_container = {}
        elif scope is AgentKind.GOVERNMENT:
            target_container = mutable_state.get("government")
            if target_container is None:
                target_container = {}
        elif scope is AgentKind.CENTRAL_BANK:
            target_container = mutable_state.get("central_bank")
            if target_container is None:
                target_container = {}
        elif scope is AgentKind.MACRO:
            target_container = mutable_state["macro"]
        elif scope is AgentKind.WORLD:
            target_container = mutable_state
        else:  # pragma: no cover - safety valve
            raise ValueError(f"Unsupported update scope: {scope}")

        if target_container is None:
            target_container = {}
        elif not isinstance(target_container, dict):
            target_container = dict(target_container)

        for path, value in update.changes.items():
            self._apply_path_value(target_container, path, value, update.mode)

        # write back for dictionary-scoped updates
        if scope is AgentKind.HOUSEHOLD:
            if resolved_key is None:  # pragma: no cover - defensive guard
                raise AssertionError("Resolved household key missing")
            key_out = str(resolved_key)
            mutable_state["households"][key_out] = target_container
        elif scope is AgentKind.FIRM:
            mutable_state["firm"] = target_container
        elif scope is AgentKind.BANK:
            mutable_state["bank"] = target_container
        elif scope is AgentKind.GOVERNMENT:
            mutable_state["government"] = target_container
        elif scope is AgentKind.CENTRAL_BANK:
            mutable_state["central_bank"] = target_container
        elif scope is AgentKind.MACRO:
            mutable_state["macro"] = target_container
        elif scope is AgentKind.WORLD:
            mutable_state.update(target_container)

    def _apply_path_value(
        self, container: Dict, path: str, value: float, mode: str
    ) -> None:
        """根据路径表达式更新嵌套字典中的目标字段。"""
        keys = path.split(".")
        cursor = container
        for key in keys[:-1]:
            next_item = cursor.get(key)
            if not isinstance(next_item, dict):
                next_item = {}
                cursor[key] = next_item
            cursor = next_item

        leaf = keys[-1]
        current_value = cursor.get(leaf)
        if mode == "delta":
            base = 0.0
            if isinstance(current_value, (int, float)):
                base = float(current_value)
            cursor[leaf] = base + value
        elif mode == "set":
            cursor[leaf] = value
        else:  # pragma: no cover - defensive branch
            raise ValueError(f"Unsupported update mode: {mode}")
