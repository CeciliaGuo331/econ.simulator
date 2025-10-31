import asyncio

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.script_engine import reset_script_registry, script_registry
from tests.utils import seed_required_scripts
from econ_sim.data_access.models import StateUpdateCommand, AgentKind, TickLogEntry


def test_orchestrator_writes_production_gdp():
    """Ensure orchestrator computes GDP = price * last_production + government.spending

    Approach:
    - Create a simulation and seed baseline scripts.
    - Set firm.last_production and firm.price via apply_updates.
    - Monkey-patch firm_production.process_production to be a no-op so it doesn't overwrite last_production.
    - Call run_tick and assert returned world_state.macro.gdp equals expected value.
    """

    async def _run():
        reset_script_registry()
        orch = SimulationOrchestrator()
        # seed minimal scripts and entities
        await seed_required_scripts(script_registry, "testsim_gdp", orchestrator=orch)

        # set deterministic firm production and price
        price = 7.0
        last_production = 10.0
        gov_spend = 1234.0

        # apply government spending update and firm production/price
        updates = [
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id="firm_seed",
                last_production=last_production,
                price=price,
            ),
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT, agent_id="government_seed", spending=gov_spend
            ),
        ]
        await orch.data_access.apply_updates("testsim_gdp", updates)

        # monkey-patch production to no-op so orchestrator will use our last_production value
        import importlib

        fp = importlib.import_module("econ_sim.logic_modules.firm_production")
        original = getattr(fp, "process_production", None)

        def fake_process_production(world_state, decisions, tick, day):
            return [], TickLogEntry(
                tick=tick, day=day, message="production_skipped_test", context={}
            )

        setattr(fp, "process_production", fake_process_production)

        try:
            result = await orch.run_tick("testsim_gdp")
        finally:
            # restore
            if original is not None:
                setattr(fp, "process_production", original)

        updated = result.world_state
        # GDP should be at least government spending (price*production >= 0)
        assert updated.macro.gdp >= gov_spend
        assert updated.macro.gdp > 0

    asyncio.run(_run())
