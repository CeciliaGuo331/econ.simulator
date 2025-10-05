"""负责驱动经济仿真 Tick 执行流程的核心调度模块。"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import List, Optional

from ..data_access.models import (
    AgentKind,
    StateUpdateCommand,
    TickDecisionOverrides,
    TickResult,
    TickLogEntry,
    WorldState,
)
from ..data_access.redis_client import DataAccessLayer, SimulationNotFoundError
from ..logic_modules.agent_logic import collect_tick_decisions, merge_tick_overrides
from ..logic_modules.market_logic import execute_tick_logic
from ..strategies.base import StrategyBundle
from ..utils.settings import get_world_config
from ..script_engine import script_registry


@dataclass
class BatchRunResult:
    """批量执行 Tick 后的结果封装。"""

    world_state: WorldState
    ticks_executed: int
    logs: List[TickLogEntry]


class SimulationOrchestrator:
    """仿真调度器，负责组织数据访问、决策生成与市场结算。"""

    def __init__(self, data_access: Optional[DataAccessLayer] = None) -> None:
        """初始化调度器。

        若未显式传入数据访问层，将使用默认的内存存储配置；同时缓存世界配置，
        方便后续 Tick 中的策略与逻辑模块复用。
        """
        config = get_world_config()
        self.data_access = data_access or DataAccessLayer.with_default_store(config)
        self.config = self.data_access.config

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

    async def list_simulations(self) -> list[str]:
        """列出已知仿真实例 ID。"""

        return await self.data_access.list_simulations()

    async def get_state(self, simulation_id: str) -> WorldState:
        """读取指定仿真实例的当前世界状态。"""
        return await self.data_access.get_world_state(simulation_id)

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
        strategies = StrategyBundle(self.config, world_state)
        script_overrides = await script_registry.generate_overrides(
            simulation_id, world_state, self.config
        )
        combined_overrides = merge_tick_overrides(script_overrides, overrides)
        decisions = await asyncio.to_thread(
            collect_tick_decisions,
            world_state,
            strategies,
            combined_overrides,
        )

        updates, logs = await asyncio.to_thread(
            execute_tick_logic,
            world_state,
            decisions,
            self.config,
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

        return await self.data_access.reset_simulation(simulation_id)

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


__all__ = ["SimulationOrchestrator", "SimulationNotFoundError", "BatchRunResult"]
