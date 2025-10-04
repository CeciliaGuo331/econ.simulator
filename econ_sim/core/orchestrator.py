"""Main simulation orchestrator coordinating tick execution."""

from __future__ import annotations

from typing import Optional

from ..data_access.models import (
    AgentKind,
    StateUpdateCommand,
    TickDecisionOverrides,
    TickResult,
    WorldState,
)
from ..data_access.redis_client import DataAccessLayer, SimulationNotFoundError
from ..logic_modules.agent_logic import collect_tick_decisions
from ..logic_modules.market_logic import execute_tick_logic
from ..strategies.base import StrategyBundle
from ..utils.settings import get_world_config


class SimulationOrchestrator:
    """High-level orchestrator controlling simulation ticks."""

    def __init__(self, data_access: Optional[DataAccessLayer] = None) -> None:
        config = get_world_config()
        self.data_access = data_access or DataAccessLayer.with_default_store(config)
        self.config = self.data_access.config

    async def create_simulation(self, simulation_id: str) -> WorldState:
        """Ensure a simulation exists, creating it if necessary."""

        return await self.data_access.ensure_simulation(simulation_id)

    async def get_state(self, simulation_id: str) -> WorldState:
        return await self.data_access.get_world_state(simulation_id)

    async def run_tick(
        self,
        simulation_id: str,
        overrides: Optional[TickDecisionOverrides] = None,
    ) -> TickResult:
        world_state = await self.create_simulation(simulation_id)
        strategies = StrategyBundle(self.config, world_state)
        decisions = collect_tick_decisions(world_state, strategies, overrides)

        updates, logs = execute_tick_logic(world_state, decisions, self.config)

        next_tick = world_state.tick + 1
        next_day = world_state.day
        if (
            next_tick - self.config.simulation.initial_tick
        ) % self.config.simulation.ticks_per_day == 0:
            next_day += 1

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
        """Reset a simulation to the initial state."""

        return await self.create_simulation(simulation_id)


__all__ = ["SimulationOrchestrator", "SimulationNotFoundError"]
