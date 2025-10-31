import asyncio, os, sys

sys.path.insert(0, os.getcwd())
from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.logic_modules import firm_production, goods_market
from econ_sim.data_access.models import (
    TickDecisions,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
    AgentKind,
)
from econ_sim.script_engine import reset_script_registry, script_registry
from tests.utils import seed_required_scripts


async def main():
    reset_script_registry()
    orch = SimulationOrchestrator()
    reset_script_registry()
    await seed_required_scripts(script_registry, "dbg_goods", orchestrator=orch)
    ws = await orch.data_access.get_world_state("dbg_goods")
    # set a firm decision
    decisions = TickDecisions(
        households={
            0: HouseholdDecision(
                labor_supply=1.0,
                consumption_budget=10.0,
                savings_rate=0.1,
                is_studying=False,
                education_payment=0.0,
            )
        },
        firm=FirmDecision(
            price=5.0, planned_production=1.0, wage_offer=1000.0, hiring_demand=1
        ),
        bank=BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0),
        government=GovernmentDecision(
            tax_rate=0.15, government_jobs=0, transfer_budget=0.0
        ),
        central_bank=CentralBankDecision(policy_rate=0.02, reserve_ratio=0.08),
    )
    # run production only
    p_updates, p_log = firm_production.process_production(
        ws, decisions, tick=ws.tick, day=ws.day
    )
    print("production log:", p_log.message, p_log.context)
    # apply production updates via data access layer so nested models are
    # hydrated back into proper Pydantic models (matches orchestrator behavior)
    updated = await orch.data_access.apply_updates("dbg_goods", p_updates)
    ws = updated
    # now call goods market
    g_updates, g_log = goods_market.clear_goods_market_new(ws, decisions)
    print("goods log:", g_log.message, g_log.context)
    print("goods updates:")
    for u in g_updates:
        print(u.scope, u.agent_id, u.changes)


if __name__ == "__main__":
    asyncio.run(main())
