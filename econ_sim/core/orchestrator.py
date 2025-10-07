"""负责驱动经济仿真 Tick 执行流程的核心调度模块。"""

from __future__ import annotations

import asyncio
import math
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, TYPE_CHECKING

from ..data_access.models import (
    AgentKind,
    HouseholdShock,
    StateUpdateCommand,
    TickDecisionOverrides,
    TickResult,
    TickLogEntry,
    SimulationFeatures,
    ScriptFailureRecord,
    WorldState,
)
from ..data_access.redis_client import DataAccessLayer, SimulationNotFoundError
from ..core.fallback_manager import BaselineFallbackManager, FallbackExecutionError
from ..logic_modules.agent_logic import collect_tick_decisions, merge_tick_overrides
from ..logic_modules.market_logic import execute_tick_logic
from ..logic_modules.shock_logic import (
    apply_household_shocks_for_decision,
    generate_household_shocks,
)
from ..utils.settings import get_world_config
from ..script_engine import script_registry
from ..script_engine.notifications import (
    LoggingScriptFailureNotifier,
    ScriptFailureNotifier,
)
from ..script_engine.registry import ScriptExecutionError, ScriptFailureEvent

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ..script_engine.registry import ScriptMetadata


logger = logging.getLogger(__name__)


@dataclass
class BatchRunResult:
    """批量执行 Tick 后的结果封装。"""

    world_state: WorldState
    ticks_executed: int
    logs: List[TickLogEntry]


class SimulationStateError(RuntimeError):
    """在仿真状态不满足操作要求时抛出的异常。"""

    def __init__(self, simulation_id: str, tick: int) -> None:
        super().__init__(
            f"Simulation {simulation_id} is at tick {tick}; operation requires tick 0."
        )
        self.simulation_id = simulation_id
        self.tick = tick


class MissingAgentScriptsError(RuntimeError):
    """当核心主体缺少脚本绑定时抛出的异常。"""

    def __init__(self, simulation_id: str, missing_agents: Iterable[AgentKind]) -> None:
        missing_list = ", ".join(sorted(agent.value for agent in missing_agents))
        super().__init__(
            f"Simulation {simulation_id} is missing required scripts for: {missing_list}"
        )
        self.simulation_id = simulation_id
        self.missing_agents = tuple(missing_agents)


class SimulationOrchestrator:
    """仿真调度器，负责组织数据访问、决策生成与市场结算。"""

    def __init__(
        self,
        data_access: Optional[DataAccessLayer] = None,
        *,
        failure_notifier: Optional[ScriptFailureNotifier] = None,
    ) -> None:
        """初始化调度器。

        若未显式传入数据访问层，将使用默认的内存存储配置；同时缓存世界配置，
        方便后续 Tick 中的策略与逻辑模块复用。
        """
        config = get_world_config()
        self.data_access = data_access or DataAccessLayer.with_default_store(config)
        self.config = self.data_access.config
        self._tick_logs: Dict[str, List[TickLogEntry]] = {}
        self._required_agents: tuple[AgentKind, ...] = (
            AgentKind.HOUSEHOLD,
            AgentKind.FIRM,
            AgentKind.BANK,
            AgentKind.GOVERNMENT,
            AgentKind.CENTRAL_BANK,
        )
        self._fallback_manager = BaselineFallbackManager()
        self._failure_notifier = (
            failure_notifier
            if failure_notifier is not None
            else LoggingScriptFailureNotifier()
        )

    async def create_simulation(self, simulation_id: str) -> WorldState:
        """确保指定 ID 的仿真实例存在。

        当实例尚未初始化时，会自动创建并返回首个世界状态快照。
        """

        return await self.data_access.ensure_simulation(simulation_id)

    async def register_participant(self, simulation_id: str, user_id: str) -> list[str]:
        """登记共享仿真会话的参与者，并返回完整参与者列表。"""

        await self.data_access.get_world_state(simulation_id)
        await self.data_access.register_participant(simulation_id, user_id)
        return await self.data_access.list_participants(simulation_id)

    async def list_participants(self, simulation_id: str) -> list[str]:
        """查询当前仿真实例的所有参与者。"""

        await self.data_access.get_world_state(simulation_id)
        return await self.data_access.list_participants(simulation_id)

    async def register_script_for_simulation(
        self,
        simulation_id: str,
        user_id: str,
        script_code: str,
        description: Optional[str] = None,
        *,
        agent_kind: AgentKind,
        entity_id: str,
    ) -> "ScriptMetadata":
        """在确保仿真处于 tick 0 的前提下上传并挂载脚本。"""

        await self._require_tick_zero(simulation_id)
        metadata = await script_registry.register_script(
            simulation_id=simulation_id,
            user_id=user_id,
            script_code=script_code,
            description=description,
            agent_kind=agent_kind,
            entity_id=entity_id,
        )
        await self._ensure_entity_seeded(metadata)
        return metadata

    async def set_script_limit(
        self, simulation_id: str, limit: Optional[int]
    ) -> Optional[int]:
        """为指定仿真实例设置每位用户的脚本数量上限。"""

        if limit is not None and limit <= 0:
            raise ValueError("script limit must be positive or null")

        await self._require_tick_zero(simulation_id)

        normalized_limit = int(limit) if limit is not None else None

        if normalized_limit is not None:
            scripts = await script_registry.list_scripts(simulation_id)
            user_counts = Counter(meta.user_id for meta in scripts)
            exceeding = [
                user for user, count in user_counts.items() if count > normalized_limit
            ]
            if exceeding:
                raise ValueError(
                    "Existing scripts exceed the requested limit for users: "
                    + ", ".join(sorted(set(exceeding)))
                )

        return await script_registry.set_simulation_limit(
            simulation_id, normalized_limit
        )

    async def get_script_limit(self, simulation_id: str) -> Optional[int]:
        """获取指定仿真实例当前生效的脚本数量上限。"""

        await self.data_access.get_world_state(simulation_id)
        return await script_registry.get_simulation_limit(simulation_id)

    async def list_simulations(self) -> list[str]:
        """列出已知仿真实例 ID。"""

        return await self.data_access.list_simulations()

    async def attach_script_to_simulation(
        self,
        simulation_id: str,
        script_id: str,
        user_id: str,
    ) -> "ScriptMetadata":
        """仅在 tick 0 时允许将脚本挂载至仿真实例。"""

        await self._require_tick_zero(simulation_id)
        metadata = await script_registry.attach_script(
            script_id=script_id,
            simulation_id=simulation_id,
            user_id=user_id,
        )
        await self._ensure_entity_seeded(metadata)
        return metadata

    async def remove_script_from_simulation(
        self,
        simulation_id: str,
        script_id: str,
    ) -> None:
        """仅在 tick 0 时允许移除已挂载的脚本。"""

        await self._require_tick_zero(simulation_id)
        scripts = await script_registry.list_scripts(simulation_id)
        target = next((meta for meta in scripts if meta.script_id == script_id), None)
        if target is None:
            raise ScriptExecutionError("Script not found for simulation")

        await script_registry.remove_script(simulation_id, script_id)
        await self.data_access.remove_entity_state(
            simulation_id, target.agent_kind, target.entity_id
        )

    async def detach_script_from_simulation(
        self,
        simulation_id: str,
        script_id: str,
        user_id: str,
    ) -> "ScriptMetadata":
        """取消脚本与仿真实例的绑定，同时移除对应实体。"""

        await self._require_tick_zero(simulation_id)
        metadata = await script_registry.get_user_script(script_id, user_id)
        if metadata.simulation_id != simulation_id:
            raise ScriptExecutionError("脚本未挂载到指定仿真实例。")

        updated = await script_registry.detach_user_script(script_id, user_id)
        await self.data_access.remove_entity_state(
            simulation_id, metadata.agent_kind, metadata.entity_id
        )
        return updated

    async def get_state(self, simulation_id: str) -> WorldState:
        """读取指定仿真实例的当前世界状态。"""
        return await self.data_access.get_world_state(simulation_id)

    async def get_recent_logs(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[TickLogEntry]:
        """返回指定仿真实例的最近 Tick 日志。"""

        await self.data_access.get_world_state(simulation_id)
        return await self.data_access.get_recent_logs(simulation_id, limit)

    async def list_recent_script_failures(
        self, simulation_id: str, limit: Optional[int] = None
    ) -> List[ScriptFailureRecord]:
        """查询指定仿真实例的脚本失败事件。"""

        await self.data_access.get_world_state(simulation_id)
        return await self.data_access.list_script_failures(simulation_id, limit)

    async def run_tick(
        self,
        simulation_id: str,
        overrides: Optional[TickDecisionOverrides] = None,
    ) -> TickResult:
        """执行一次完整的 Tick。

        主要步骤包括：确保仿真存在、生成默认策略决策、应用玩家覆盖、调用市场逻辑
        计算状态更新，并最终写回数据存储，同时返回此次 Tick 的日志和更新详情。
        """
        world_state = await self.create_simulation(simulation_id)
        await self._require_agent_coverage(simulation_id)

        shocks: Dict[int, HouseholdShock] = {}
        decision_state = world_state
        if world_state.features.household_shock_enabled:
            shocks = generate_household_shocks(world_state, self.config)
            decision_state = apply_household_shocks_for_decision(world_state, shocks)

        try:
            baseline_decisions = self._fallback_manager.generate_decisions(
                decision_state, self.config
            )
        except FallbackExecutionError as exc:
            logger.exception(
                "Failed to generate fallback decisions for simulation %s",
                simulation_id,
            )
            raise RuntimeError(
                "Baseline fallback failed to produce required decisions"
            ) from exc

        (
            script_overrides,
            script_failure_logs,
            failure_events,
        ) = await script_registry.generate_overrides(
            simulation_id, decision_state, self.config
        )
        self._dispatch_script_failures(failure_events)
        if failure_events:
            await self.data_access.record_script_failures(failure_events)
        combined_overrides = merge_tick_overrides(script_overrides, overrides)
        decisions = await asyncio.to_thread(
            collect_tick_decisions,
            baseline_decisions,
            combined_overrides,
        )

        updates, logs = await asyncio.to_thread(
            execute_tick_logic,
            world_state,
            decisions,
            self.config,
            shocks,
        )

        if script_failure_logs:
            logs = script_failure_logs + logs

        if world_state.features.household_shock_enabled and shocks:
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.WORLD,
                    agent_id=None,
                    household_shocks={
                        hid: shock.model_dump() for hid, shock in shocks.items()
                    },
                )
            )
        elif world_state.household_shocks:
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.WORLD,
                    agent_id=None,
                    household_shocks={},
                )
            )

        next_tick = world_state.tick + 1
        sim_config = self.config.simulation
        ticks_since_start = next_tick - sim_config.initial_tick
        if ticks_since_start <= 0:
            next_day = sim_config.initial_day
        else:
            next_day = sim_config.initial_day + math.ceil(
                ticks_since_start / sim_config.ticks_per_day
            )
        next_day = max(next_day, world_state.day)

        updates.append(
            StateUpdateCommand.assign(
                AgentKind.WORLD,
                agent_id=None,
                tick=next_tick,
                day=next_day,
            )
        )

        updated_state = await self.data_access.apply_updates(simulation_id, updates)
        tick_result = TickResult(world_state=updated_state, logs=logs, updates=updates)
        await self.data_access.record_tick(tick_result)
        return tick_result

    async def reset_simulation(self, simulation_id: str) -> WorldState:
        """将仿真实例恢复到初始状态。

        该操作会重建世界状态的初始快照，但不会影响脚本注册或参与者信息。
        """
        previous_features: Optional[SimulationFeatures] = None
        try:
            current = await self.data_access.get_world_state(simulation_id)
            previous_features = current.features.model_copy(deep=True)
        except SimulationNotFoundError:
            previous_features = None

        state = await self.data_access.reset_simulation(simulation_id)
        self._tick_logs[simulation_id] = []

        if previous_features is not None:
            state = await self.update_simulation_features(
                simulation_id,
                **previous_features.model_dump(),
            )

        return state

    async def get_simulation_features(self, simulation_id: str) -> SimulationFeatures:
        state = await self.data_access.get_world_state(simulation_id)
        return state.features

    async def update_simulation_features(
        self,
        simulation_id: str,
        **updates: object,
    ) -> WorldState:
        state = await self._require_tick_zero(simulation_id)
        features = state.features.model_copy(deep=True)

        allowed_fields = set(features.model_dump().keys())
        mutated = False
        for field, value in updates.items():
            if field not in allowed_fields or value is None:
                continue
            setattr(features, field, value)
            mutated = True

        if not mutated:
            return state

        updated = await self.data_access.apply_updates(
            simulation_id,
            [
                StateUpdateCommand.assign(
                    AgentKind.WORLD,
                    agent_id=None,
                    features=features.model_dump(),
                )
            ],
        )

        if not features.household_shock_enabled:
            updated = await self.data_access.apply_updates(
                simulation_id,
                [
                    StateUpdateCommand.assign(
                        AgentKind.WORLD,
                        agent_id=None,
                        household_shocks={},
                    )
                ],
            )

        return updated

    async def run_until_day(self, simulation_id: str, days: int) -> BatchRunResult:
        """自动执行多个 Tick，直到完成指定天数的全部 Tick。"""

        if days <= 0:
            raise ValueError("days must be a positive integer")

        state = await self.create_simulation(simulation_id)
        ticks_per_day = self.config.simulation.ticks_per_day
        target_tick = state.tick + days * ticks_per_day

        ticks_executed = 0
        aggregated_logs: List[TickLogEntry] = []
        last_result: Optional[TickResult] = None

        safety_limit = max(days * ticks_per_day * 5, ticks_per_day * 2)

        while True:
            if state.tick >= target_tick:
                break

            last_result = await self.run_tick(simulation_id)
            state = last_result.world_state
            ticks_executed += 1
            aggregated_logs.extend(last_result.logs)

            if ticks_executed % 5 == 0:
                await asyncio.sleep(0)

            if ticks_executed > safety_limit:
                raise RuntimeError(
                    "Exceeded expected number of ticks while advancing simulation"
                )

        return BatchRunResult(
            world_state=state,
            ticks_executed=ticks_executed,
            logs=aggregated_logs,
        )

    async def delete_simulation(self, simulation_id: str) -> dict[str, int]:
        """删除仿真实例的世界状态，并解除与参与者、脚本的关联。"""

        participants_removed = await self.data_access.delete_simulation(simulation_id)
        scripts_removed = await script_registry.detach_simulation(simulation_id)
        return {
            "participants_removed": participants_removed,
            "scripts_detached": scripts_removed,
        }

    async def _require_agent_coverage(self, simulation_id: str) -> None:
        state = await self.data_access.get_world_state(simulation_id)
        present = set()
        if state.households:
            present.add(AgentKind.HOUSEHOLD)
        if state.firm is not None:
            present.add(AgentKind.FIRM)
        if state.bank is not None:
            present.add(AgentKind.BANK)
        if state.government is not None:
            present.add(AgentKind.GOVERNMENT)
        if state.central_bank is not None:
            present.add(AgentKind.CENTRAL_BANK)

        scripts = await script_registry.list_scripts(simulation_id)
        scripted_kinds = {meta.agent_kind for meta in scripts}

        missing: list[AgentKind] = []
        for agent in self._required_agents:
            if agent not in present or agent not in scripted_kinds:
                missing.append(agent)

        if AgentKind.HOUSEHOLD not in missing and state.households:
            scripted_households = {
                meta.entity_id
                for meta in scripts
                if meta.agent_kind is AgentKind.HOUSEHOLD
            }
            expected_households = {
                str(identifier) for identifier in state.households.keys()
            }
            if not expected_households.issubset(scripted_households):
                missing.append(AgentKind.HOUSEHOLD)

        if missing:
            raise MissingAgentScriptsError(simulation_id, missing)

    async def _require_tick_zero(self, simulation_id: str) -> WorldState:
        """确保仿真实例仍处于 tick 0 状态，否则抛出异常。"""

        state = await self.data_access.get_world_state(simulation_id)
        if state.tick != 0:
            raise SimulationStateError(simulation_id, state.tick)
        return state

    async def _ensure_entity_seeded(self, metadata: "ScriptMetadata") -> None:
        if metadata.simulation_id is None:
            return
        await self.data_access.ensure_entity_state(
            metadata.simulation_id,
            metadata.agent_kind,
            metadata.entity_id,
        )

    def _dispatch_script_failures(self, events: List[ScriptFailureEvent]) -> None:
        if not events:
            return
        for event in events:
            try:
                self._failure_notifier.notify(event)
            except Exception:  # pragma: no cover - best effort logging
                logger.exception(
                    "Failed to dispatch script failure notification for %s",
                    event.script_id,
                )


__all__ = [
    "SimulationOrchestrator",
    "SimulationNotFoundError",
    "SimulationStateError",
    "BatchRunResult",
]
