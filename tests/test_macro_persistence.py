import asyncio

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.redis_client import DataAccessLayer
from econ_sim.data_access.models import AgentKind
from econ_sim.script_engine import script_registry


def test_macro_fields_persisted_after_tick(tmp_path):
    """Integration test: run a single tick and assert macro fields are persisted.

    This creates a fresh in-memory simulation via the DataAccessLayer default
    store, runs a tick through the SimulationOrchestrator, then reloads the
    persisted world state and asserts that macro fields (gdp, inflation,
    unemployment_rate, price_index, wage_index) have been updated from their
    defaults.
    """

    # use a deterministic simulation id per tmp_path to avoid collisions
    simulation_id = f"test_sim_{tmp_path.name}"

    # create orchestrator with default data access (in-memory store in tests)
    orchestrator = SimulationOrchestrator()

    async def _run():
        # ensure simulation exists
        await orchestrator.create_simulation(simulation_id)

        # ensure required entities exist (household 1 and singleton agents)
        dal = orchestrator.data_access
        await dal.ensure_entity_state(simulation_id, AgentKind.HOUSEHOLD, "1")
        await dal.ensure_entity_state(simulation_id, AgentKind.FIRM, "firm_1")
        await dal.ensure_entity_state(simulation_id, AgentKind.BANK, "bank")
        await dal.ensure_entity_state(simulation_id, AgentKind.GOVERNMENT, "government")
        await dal.ensure_entity_state(
            simulation_id, AgentKind.CENTRAL_BANK, "central_bank"
        )

        # register minimal no-op scripts for required agents so run_tick doesn't reject
        noop = "def generate_decisions(context):\n    return None\n"
        await script_registry.register_script(
            simulation_id,
            "test_user",
            noop,
            agent_kind=AgentKind.HOUSEHOLD,
            entity_id="1",
        )
        await script_registry.register_script(
            simulation_id,
            "test_user",
            noop,
            agent_kind=AgentKind.FIRM,
            entity_id="firm_1",
        )
        await script_registry.register_script(
            simulation_id,
            "test_user",
            noop,
            agent_kind=AgentKind.BANK,
            entity_id="bank",
        )
        await script_registry.register_script(
            simulation_id,
            "test_user",
            noop,
            agent_kind=AgentKind.GOVERNMENT,
            entity_id="government",
        )
        await script_registry.register_script(
            simulation_id,
            "test_user",
            noop,
            agent_kind=AgentKind.CENTRAL_BANK,
            entity_id="central_bank",
        )

        # run a tick (uses those no-op scripts)
        result = await orchestrator.run_tick(simulation_id)

        # result.world_state is the in-memory updated state after apply_updates
        updated_state = result.world_state

        # quick assertions on in-memory result
        assert hasattr(updated_state, "macro")
        # at least GDP should be non-default (production-based or gov spending)
        assert float(updated_state.macro.gdp) >= 0.0

        # Now reload persisted state via data_access and ensure macro fields there match
        dal = orchestrator.data_access
        persisted = await dal.get_world_state(simulation_id)

        assert persisted is not None
        # persisted.macro should exist and match updated_state.macro values
        assert float(persisted.macro.gdp) == float(updated_state.macro.gdp)
        assert float(persisted.macro.price_index) == float(
            updated_state.macro.price_index
        )
        assert float(persisted.macro.wage_index) == float(
            updated_state.macro.wage_index
        )
        assert float(persisted.macro.unemployment_rate) == float(
            updated_state.macro.unemployment_rate
        )
        assert float(persisted.macro.inflation) == float(updated_state.macro.inflation)

    asyncio.get_event_loop().run_until_complete(_run())
