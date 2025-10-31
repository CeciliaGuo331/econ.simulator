import asyncio, os, sys

sys.path.insert(0, os.getcwd())
from econ_sim.core.orchestrator import _execute_market_logic
from econ_sim.script_engine import reset_script_registry, script_registry
from econ_sim.core.orchestrator import SimulationOrchestrator
from tests.utils import seed_required_scripts
from econ_sim.data_access.models import AgentKind, StateUpdateCommand
from econ_sim.data_access.models import (
    TickDecisions,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)
from econ_sim.utils.settings import get_world_config


async def main():
    reset_script_registry()
    registry = script_registry
    orch = SimulationOrchestrator()
    await seed_required_scripts(registry, "dbg2", orchestrator=orch)
    ws = await orch.create_simulation("dbg2")
    await orch.data_access.ensure_entity_state("dbg2", AgentKind.HOUSEHOLD, "0")
    ws = await orch.data_access.get_world_state("dbg2")
    households = {
        0: HouseholdDecision(
            labor_supply=1.0,
            consumption_budget=10.0,
            savings_rate=0.1,
            is_studying=False,
            education_payment=0.0,
        )
    }
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
    cfg = get_world_config()
    updates, logs, ledgers, signals = _execute_market_logic(ws, decisions, cfg, {})
    print("LOGS:")
    for l in logs:
        print(l.message, l.context)
    print("\nUPDATES:")
    macro_found = False
    for u in updates:
        print(u.scope, u.agent_id, u.changes)
        try:
            if getattr(u, "scope", None) == AgentKind.MACRO:
                macro_found = True
        except Exception:
            pass
    print("\nMACRO update found:", macro_found)


if __name__ == "__main__":
    asyncio.run(main())
