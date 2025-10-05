"""基于 Redis（并提供内存后备）的异步数据访问层实现。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, Set

import numpy as np

try:  # pragma: no cover - optional dependency at runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - fallback when redis isn't installed yet
    Redis = None  # type: ignore

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


class SimulationNotFoundError(RuntimeError):
    """当访问不存在的仿真实例时抛出的异常。"""


class StateStore(Protocol):
    """通用状态存储接口，抽象出加载与保存操作。"""

    async def load(self, simulation_id: str) -> Optional[Dict]:
        """根据仿真 ID 异步读取状态快照，若不存在则返回 ``None``。"""

    async def store(self, simulation_id: str, payload: Dict) -> None:
        """将状态快照持久化到存储介质。"""


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


@dataclass
class DataAccessLayer:
    """为调度器提供统一入口的数据访问外观类。"""

    config: WorldConfig
    store: StateStore
    _participants: Dict[str, Set[str]] = field(default_factory=dict)
    _known_simulations: Set[str] = field(default_factory=set)

    @classmethod
    def with_default_store(
        cls, config: Optional[WorldConfig] = None
    ) -> "DataAccessLayer":
        """使用默认配置与内存存储构建数据访问层实例。"""
        return cls(config=config or get_world_config(), store=InMemoryStateStore())

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
