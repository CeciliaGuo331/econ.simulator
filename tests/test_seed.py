import pytest

from econ_sim.auth.user_manager import (
    InMemorySessionStore,
    InMemoryUserStore,
    UserManager,
)
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import AgentKind
from econ_sim.script_engine import ScriptRegistry
from econ_sim.script_engine.test_world_seed import (
    TEST_WORLD_DEFAULT_HOUSEHOLDS,
    seed_test_world,
)


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

    expected_households = max(
        TEST_WORLD_DEFAULT_HOUSEHOLDS,
        orchestrator.config.simulation.num_households,
    )
    assert summary.total_scripts == expected_households + 4
    assert summary.scripts_created == expected_households + 4

    scripts = await registry.list_scripts("seeded-sim")
    assert len(scripts) == expected_households + 4

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
    assert len(state.households) == expected_households


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

    expected_households = max(
        TEST_WORLD_DEFAULT_HOUSEHOLDS,
        orchestrator.config.simulation.num_households,
    )
    assert summary.scripts_created == 0
    assert summary.total_scripts == summary.scripts_existing
    assert summary.total_scripts == expected_households + 4
    assert summary.users_created == 0
    assert summary.total_users == summary.users_existing


@pytest.mark.asyncio
async def test_seed_test_world_can_execute_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = SimulationOrchestrator()
    registry = ScriptRegistry()
    user_manager = UserManager(InMemoryUserStore(), InMemorySessionStore())

    import econ_sim.script_engine as script_engine_module
    import econ_sim.core.orchestrator as orchestrator_module
    import econ_sim.script_engine.test_world_seed as seed_module

    monkeypatch.setattr(script_engine_module, "script_registry", registry)
    monkeypatch.setattr(orchestrator_module, "script_registry", registry)
    monkeypatch.setattr(seed_module, "default_registry", registry)

    summary = await seed_test_world(
        simulation_id="tick-sim",
        household_count=5,
        orchestrator=orchestrator,
        registry=registry,
        user_manager=user_manager,
        overwrite_existing=True,
    )

    result = await orchestrator.run_tick("tick-sim")
    expected_households = max(
        TEST_WORLD_DEFAULT_HOUSEHOLDS,
        orchestrator.config.simulation.num_households,
    )

    assert result.world_state.tick == 1
    assert len(result.world_state.households) == expected_households
    assert summary.total_scripts == expected_households + 4
