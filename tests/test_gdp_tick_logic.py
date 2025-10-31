import pytest

from econ_sim.core.orchestrator import _execute_market_logic
from econ_sim.utils.settings import get_world_config
from econ_sim.data_access.models import (
    TickDecisions,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)

from econ_sim.script_engine import reset_script_registry, script_registry
from econ_sim.core.orchestrator import SimulationOrchestrator
from tests.utils import seed_required_scripts
from econ_sim.data_access.models import StateUpdateCommand, AgentKind


@pytest.mark.asyncio
async def test_execute_market_logic_updates_gdp():
    # prepare environment and simulation
    reset_script_registry()
    registry = script_registry
    orch = SimulationOrchestrator()
    await seed_required_scripts(registry, "logic-sim", orchestrator=orch)
    ws = await orch.create_simulation("logic-sim")

    # ensure a household exists
    await orch.data_access.ensure_entity_state("logic-sim", AgentKind.HOUSEHOLD, "0")
    # reload world state so decisions and world_state align
    ws = await orch.data_access.get_world_state("logic-sim")

    # craft decisions that request hiring and household labour supply
    households = {
        0: HouseholdDecision(
            labor_supply=1.0,
            consumption_budget=10.0,
            savings_rate=0.1,
            is_studying=False,
            education_payment=0.0,
        )
    }
    # set a very high wage_offer so reservation wage filters do not exclude candidates
    firm_dec = FirmDecision(
        price=5.0, planned_production=1.0, wage_offer=1000.0, hiring_demand=1
    )
    bank_dec = BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0)
    gov_dec = GovernmentDecision(tax_rate=0.15, government_jobs=0, transfer_budget=0.0)
    cb_dec = CentralBankDecision(policy_rate=0.02, reserve_ratio=0.08)

    decisions = TickDecisions(
        households=households,
        firm=firm_dec,
        bank=bank_dec,
        government=gov_dec,
        central_bank=cb_dec,
    )

    config = get_world_config()
    updates, logs, ledgers, signals = _execute_market_logic(ws, decisions, config, {})

    # Apply updates to persistent world state
    updated = await orch.data_access.apply_updates("logic-sim", updates)

    # Check GDP
    gdp = updated.macro.gdp
    assert gdp is not None
    assert float(gdp) >= 0.0
    # if production or gov spending >0 then gdp should reflect that
    assert float(gdp) >= float(updated.government.spending)
