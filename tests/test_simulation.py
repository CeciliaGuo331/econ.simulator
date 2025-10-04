import pytest

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import (
    FirmDecisionOverride,
    HouseholdDecisionOverride,
    TickDecisionOverrides,
)


@pytest.mark.asyncio
async def test_tick_progression_increments() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "test_sim"
    state = await orchestrator.create_simulation(simulation_id)
    result = await orchestrator.run_tick(simulation_id)

    assert result.world_state.tick == state.tick + 1
    assert result.world_state.day >= state.day
    assert result.world_state.macro.gdp >= 0.0


@pytest.mark.asyncio
async def test_overrides_affect_decisions() -> None:
    orchestrator = SimulationOrchestrator()
    simulation_id = "override_sim"
    await orchestrator.create_simulation(simulation_id)

    overrides = TickDecisionOverrides(
        households={
            0: HouseholdDecisionOverride(consumption_budget=0.0, savings_rate=0.0)
        },
        firm=FirmDecisionOverride(price=25.0),
    )

    result = await orchestrator.run_tick(simulation_id, overrides=overrides)

    assert result.world_state.households[0].last_consumption <= 0.5
    assert abs(result.world_state.firm.price - 25.0) < 1e-6
