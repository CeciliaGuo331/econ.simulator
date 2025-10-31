import asyncio
import os

import pytest

from econ_sim.script_engine import reset_script_registry, script_registry
from econ_sim.core.orchestrator import SimulationOrchestrator
from tests.utils import seed_required_scripts
from econ_sim.data_access.models import StateUpdateCommand, AgentKind


@pytest.mark.asyncio
async def test_run_one_tick_and_gdp_nonzero():
    # Ensure script registry uses a slightly larger timeout for test
    os.environ["ECON_SIM_SCRIPT_TIMEOUT_SECONDS"] = "1.0"
    reset_script_registry()
    registry = script_registry

    orchestrator = SimulationOrchestrator()

    # seed baseline scripts and create simulation
    await seed_required_scripts(registry, "e2e-sim", orchestrator=orchestrator)
    ws = await orchestrator.create_simulation("e2e-sim")

    # ensure at least one household exists and is assigned as a firm employee
    await orchestrator.data_access.ensure_entity_state(
        "e2e-sim", AgentKind.HOUSEHOLD, "0"
    )
    # attach household 0 as employee to the firm
    firm_id = ws.firm.id
    await orchestrator.data_access.apply_updates(
        "e2e-sim",
        [
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id=firm_id,
                employees=[0],
                balance_sheet=ws.firm.balance_sheet.model_dump(),
            )
        ],
    )

    # set a known government spending to validate GDP >= government spending
    await orchestrator.data_access.apply_updates(
        "e2e-sim",
        [
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=ws.government.id,
                spending=500.0,
                balance_sheet=ws.government.balance_sheet.model_dump(),
            )
        ],
    )

    # run a single tick
    result = await orchestrator.run_tick("e2e-sim")

    gdp = getattr(result.world_state.macro, "gdp", None)
    assert gdp is not None
    # GDP should be at least government spending, and positive
    assert float(gdp) >= 500.0
    assert float(gdp) > 0.0
