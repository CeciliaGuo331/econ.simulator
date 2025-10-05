"""异步数据访问层及其缓存/持久化存储实现。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Set, TYPE_CHECKING

import numpy as np

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
else:  # pragma: no cover - runtime fallback when asyncpg is unavailable
    AsyncpgPool = Any  # type: ignore

from .models import (
    AgentKind,
    BalanceSheet,
    BankState,
    CentralBankState,
    FirmState,
    GovernmentState,
    HouseholdState,
    MacroState,
    StateUpdateCommand,
    TickResult,
    WorldState,
)
from ..utils.settings import WorldConfig, get_world_config


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
            return json.loads(json.dumps(snapshot)) if snapshot is not None else None

    async def store(self, simulation_id: str, payload: Dict) -> None:
        """将仿真状态深拷贝后写入内存字典。"""
        async with self._lock:
            self._storage[simulation_id] = json.loads(json.dumps(payload))

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

        cache_error: Optional[Exception] = None
        if self._cache is not None:
            try:
                await self._cache.store(simulation_id, payload)
            except (
                Exception
            ) as exc:  # pragma: no cover - cache update failure is non-fatal
                cache_error = exc
                logger.warning(
                    "Failed to update cache for simulation %s",
                    simulation_id,
                    exc_info=exc,
                )

        if self._persistent is not None:
            try:
                await self._persistent.store(simulation_id, payload)
            except Exception as exc:
                raise PersistenceError(
                    "Failed to persist world state to primary store"
                ) from exc
        elif self._fallback is not None:
            await self._fallback.store(simulation_id, payload)
        elif self._cache is None:
            raise PersistenceError(
                "No persistent store configured for simulation state"
            )

        if self._fallback is not None and self._persistent is not None:
            try:
                await self._fallback.store(simulation_id, payload)
            except Exception as exc:  # pragma: no cover - fallback warming warning
                logger.warning(
                    "Failed to update fallback store for simulation %s",
                    simulation_id,
                    exc_info=exc,
                )

        if cache_error is not None and self._persistent is None:
            raise PersistenceError(
                "Cache update failed without persistent backup"
            ) from cache_error

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


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(identifier: str) -> str:
    """以安全方式引用 SQL 标识符。"""

    if not _IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    return f'"{identifier}"'


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

    async def _get_pool(self) -> AsyncpgPool:
        if self._pool is not None:
            return self._pool

        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            self._pool = await asyncpg.create_pool(  # type: ignore[attr-defined]
                dsn=self._dsn,
                min_size=self._min_pool_size,
                max_size=self._max_pool_size,
            )
            await self._ensure_schema()
        return self._pool

    async def _ensure_schema(self) -> None:
        assert self._pool is not None  # nosec - ensured by caller
        schema_ident = _quote_identifier(self._schema)
        table_ident = _quote_identifier(self._table)
        qualified_table = f"{schema_ident}.{table_ident}"
        index_ident = _quote_identifier(f"{self._table}_updated_at_idx")

        async with self._pool.acquire() as conn:
            if self._create_schema:
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ident}")
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

        self._qualified_table = qualified_table

    async def load(self, simulation_id: str) -> Optional[Dict]:
        pool = await self._get_pool()
        if self._qualified_table is None:  # pragma: no cover - defensive guard
            raise RuntimeError("PostgresStateStore schema not initialized")
        async with pool.acquire() as conn:
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
        if self._qualified_table is None:  # pragma: no cover - defensive guard
            raise RuntimeError("PostgresStateStore schema not initialized")
        tick = int(payload.get("tick", 0))
        day = int(payload.get("day", 0))
        data = json.dumps(payload)
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._qualified_table} (simulation_id, tick, day, payload, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, timezone('utc', now()))
                ON CONFLICT (simulation_id)
                DO UPDATE SET
                    tick = EXCLUDED.tick,
                    day = EXCLUDED.day,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                simulation_id,
                tick,
                day,
                data,
            )

    async def delete(self, simulation_id: str) -> None:
        pool = await self._get_pool()
        if self._qualified_table is None:  # pragma: no cover - defensive guard
            raise RuntimeError("PostgresStateStore schema not initialized")
        async with pool.acquire() as conn:
            await conn.execute(
                f"DELETE FROM {self._qualified_table} WHERE simulation_id = $1",
                simulation_id,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


@dataclass
class DataAccessLayer:
    """为调度器提供统一入口的数据访问外观类。"""

    config: WorldConfig
    store: StateStore
    cache_store: Optional[StateStore] = None
    persistent_store: Optional[StateStore] = None
    fallback_store: Optional[StateStore] = None
    _participants: Dict[str, Set[str]] = field(default_factory=dict)
    _known_simulations: Set[str] = field(default_factory=set)

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

        cache_store: Optional[StateStore] = None
        persistent_store: Optional[StateStore] = None

        if redis_url and Redis is not None:
            redis_client = Redis.from_url(
                redis_url, encoding="utf-8", decode_responses=False
            )
            cache_store = RedisStateStore(redis_client, prefix=redis_prefix)

        if postgres_dsn:
            persistent_store = PostgresStateStore(
                postgres_dsn,
                schema=pg_schema,
                table=pg_table,
            )

        if cache_store or persistent_store:
            composite = CompositeStateStore(
                cache=cache_store,
                persistent=persistent_store,
                fallback=fallback,
            )
            return cls(
                config=resolved_config,
                store=composite,
                cache_store=cache_store,
                persistent_store=persistent_store,
                fallback_store=fallback,
            )

        return cls(config=resolved_config, store=fallback, fallback_store=fallback)

    async def ensure_simulation(self, simulation_id: str) -> WorldState:
        """确保仿真实例存在，不存在时按配置创建初始世界状态。"""
        existing = await self.store.load(simulation_id)
        if existing is not None:
            world_state = WorldState.model_validate(existing)
            self._known_simulations.add(simulation_id)
            return world_state

        world_state = self._build_initial_world_state(simulation_id)
        await self._persist_state(world_state)
        return world_state

    async def reset_simulation(self, simulation_id: str) -> WorldState:
        """无论当前状态如何，重新生成初始世界状态并覆盖存储。"""

        world_state = self._build_initial_world_state(simulation_id)
        await self._persist_state(world_state)
        return world_state

    async def delete_simulation(self, simulation_id: str) -> int:
        """彻底移除指定仿真实例的世界状态，并返回解除关联的参与者数量。"""

        existing = await self.store.load(simulation_id)
        if existing is None:
            raise SimulationNotFoundError(f"Simulation '{simulation_id}' not found")

        await self.store.delete(simulation_id)
        participants = self._participants.pop(simulation_id, set())
        self._known_simulations.discard(simulation_id)
        return len(participants)

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

    async def record_tick(self, tick_result: TickResult) -> None:
        """记录仿真步执行结果，当前仅持久化最新世界状态。"""
        await self._persist_state(tick_result.world_state)

    def register_participant(self, simulation_id: str, user_id: str) -> None:
        """登记参与同一仿真实例的用户，用于共享会话管理。"""

        participants = self._participants.setdefault(simulation_id, set())
        participants.add(user_id)

    def list_participants(self, simulation_id: str) -> list[str]:
        """返回已登记的参与者列表。"""

        return sorted(self._participants.get(simulation_id, set()))

    def list_simulations(self) -> list[str]:
        """返回已知的仿真实例 ID 列表。"""

        return sorted(self._known_simulations)

    async def _persist_state(self, world_state: WorldState) -> None:
        """将世界状态写回底层存储。"""
        await self.store.store(world_state.simulation_id, world_state.model_dump())
        self._known_simulations.add(world_state.simulation_id)

    def _build_initial_world_state(self, simulation_id: str) -> WorldState:
        """依据配置构造新的初始世界状态。"""
        sim_cfg = self.config.simulation
        markets = self.config.markets
        policies = self.config.policies
        rng = np.random.default_rng(sim_cfg.seed)

        households: Dict[int, HouseholdState] = {}
        for idx in range(sim_cfg.num_households):
            skill = float(max(0.4, rng.normal(1.0, 0.15)))
            preference = float(np.clip(rng.normal(0.5, 0.1), 0.2, 0.8))
            cash = float(rng.uniform(200.0, 400.0))
            deposits = float(rng.uniform(100.0, 200.0))
            households[idx] = HouseholdState(
                id=idx,
                balance_sheet=BalanceSheet(
                    cash=cash,
                    deposits=deposits,
                    loans=0.0,
                    inventory_goods=float(np.clip(rng.normal(2.0, 1.0), 0.0, 10.0)),
                ),
                skill=skill,
                preference=preference,
                reservation_wage=float(
                    np.clip(markets.labor.base_wage * skill * 0.8, 40.0, 120.0)
                ),
            )

        firm_state = FirmState(
            balance_sheet=BalanceSheet(
                cash=50000.0,
                deposits=10000.0,
                loans=0.0,
                inventory_goods=float(
                    sim_cfg.num_households * markets.goods.subsistence_consumption * 2
                ),
            ),
            price=markets.goods.base_price,
            wage_offer=markets.labor.base_wage,
            productivity=float(np.clip(rng.normal(1.0, 0.1), 0.6, 1.4)),
            employees=[],
        )

        government_state = GovernmentState(
            balance_sheet=BalanceSheet(
                cash=100000.0, deposits=0.0, loans=0.0, inventory_goods=0.0
            ),
            tax_rate=policies.tax_rate,
            unemployment_benefit=policies.unemployment_benefit,
            spending=policies.government_spending,
        )

        bank_state = BankState(
            balance_sheet=BalanceSheet(
                cash=200000.0,
                deposits=float(
                    sum(h.balance_sheet.deposits for h in households.values())
                ),
                loans=0.0,
                inventory_goods=0.0,
            ),
            deposit_rate=self.config.markets.finance.deposit_rate,
            loan_rate=self.config.markets.finance.loan_rate,
        )

        central_bank_state = CentralBankState(
            base_rate=self.config.policies.central_bank.base_rate,
            reserve_ratio=self.config.policies.central_bank.reserve_ratio,
            inflation_target=self.config.policies.central_bank.inflation_target,
            unemployment_target=self.config.policies.central_bank.unemployment_target,
        )

        macro_state = MacroState(
            gdp=0.0,
            inflation=0.0,
            unemployment_rate=1.0,
            price_index=100.0,
            wage_index=100.0,
        )

        return WorldState(
            simulation_id=simulation_id,
            tick=sim_cfg.initial_tick,
            day=sim_cfg.initial_day,
            households=households,
            firm=firm_state,
            bank=bank_state,
            government=government_state,
            central_bank=central_bank_state,
            macro=macro_state,
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
            target_container = mutable_state["firm"]
        elif scope is AgentKind.BANK:
            target_container = mutable_state["bank"]
        elif scope is AgentKind.GOVERNMENT:
            target_container = mutable_state["government"]
        elif scope is AgentKind.CENTRAL_BANK:
            target_container = mutable_state["central_bank"]
        elif scope is AgentKind.MACRO:
            target_container = mutable_state["macro"]
        elif scope is AgentKind.WORLD:
            target_container = mutable_state
        else:  # pragma: no cover - safety valve
            raise ValueError(f"Unsupported update scope: {scope}")

        if not isinstance(target_container, dict):
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
