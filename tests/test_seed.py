import pytest

from econ_sim.auth.user_manager import (
    InMemorySessionStore,
    InMemoryUserStore,
    UserManager,
)
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import AgentKind
from econ_sim.script_engine import ScriptRegistry
from econ_sim.script_engine.test_world_seed import seed_test_world


@pytest.mark.asyncio
async def test_seed_test_world_provisions_all_agents() -> None:
    orchestrator = SimulationOrchestrator()
    registry = ScriptRegistry()
    user_manager = UserManager(InMemoryUserStore(), InMemorySessionStore())

    summary = await seed_test_world(
        simulation_id="seeded-sim",
        household_count=5,
        orchestrator=orchestrator,
        registry=registry,
        user_manager=user_manager,
    )

    target_households = max(
        5,
        orchestrator.config.simulation.num_households,
    )
    assert summary.total_scripts == target_households + 4
    assert summary.scripts_created == target_households + 4

    scripts = await registry.list_scripts("seeded-sim")
    assert len(scripts) == target_households + 4

    kinds = {meta.agent_kind for meta in scripts}
    for required in (
        AgentKind.HOUSEHOLD,
        AgentKind.FIRM,
        AgentKind.BANK,
        AgentKind.GOVERNMENT,
        AgentKind.CENTRAL_BANK,
    ):
        assert required in kinds

    state = await orchestrator.get_state("seeded-sim")
    assert state.firm is not None
    assert state.bank is not None
    assert state.government is not None
    assert state.central_bank is not None
    assert len(state.households) == target_households


@pytest.mark.asyncio
async def test_seed_test_world_is_idempotent() -> None:
    orchestrator = SimulationOrchestrator()
    registry = ScriptRegistry()
    user_manager = UserManager(InMemoryUserStore(), InMemorySessionStore())

    await seed_test_world(
        simulation_id="repeat-sim",
        household_count=5,
        orchestrator=orchestrator,
        registry=registry,
        user_manager=user_manager,
    )

    summary = await seed_test_world(
        simulation_id="repeat-sim",
        household_count=5,
        orchestrator=orchestrator,
        registry=registry,
        user_manager=user_manager,
    )

    assert summary.scripts_created == 0
    assert summary.total_scripts == summary.scripts_existing
    assert summary.users_created == 0
    assert summary.total_users == summary.users_existing
