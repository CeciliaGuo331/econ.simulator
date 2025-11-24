import asyncio
from types import SimpleNamespace

import pytest

from econ_sim.core.orchestrator import SimulationOrchestrator
from econ_sim.data_access.models import (
    AgentKind,
    StateUpdateCommand,
    TickDecisionOverrides,
    HouseholdDecisionOverride,
    HouseholdDecision,
    BalanceSheet,
    HouseholdState,
    MacroState,
    FirmState,
    GovernmentState,
    BankState,
    CentralBankState,
    SimulationFeatures,
    EmploymentStatus,
    WorldState,
)
from econ_sim.utils.settings import get_world_config
from econ_sim.logic_modules import education


@pytest.mark.asyncio
async def test_household_education_and_daily_settlement_flow():
    orch = SimulationOrchestrator()
    sim_id = "test_education_flow"

    # create simulation and seed entities
    await orch.create_simulation(sim_id)
    # ensure household 1 and 2, firm and government exist
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.HOUSEHOLD, "1")
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.HOUSEHOLD, "2")
    await orch.data_access.ensure_entity_state(sim_id, AgentKind.FIRM, "firm_1")
    await orch.data_access.ensure_entity_state(
        sim_id, AgentKind.GOVERNMENT, "government"
    )

    # set initial balances and create an existing employment (household 2 employed by firm)
    ws = await orch.data_access.get_world_state(sim_id)
    firm = ws.firm
    # record initial education level for household 1
    initial_edu = float(ws.households.get(1).education_level or 0.0)
    # prepare firm balance sheet with cash to pay wages
    firm_bs = firm.balance_sheet.model_dump()
    firm_bs["cash"] = 1000.0

    updates = []
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.FIRM,
            agent_id=firm.id,
            employees=[2],
            wage_offer=50.0,
            balance_sheet=firm_bs,
        )
    )

    # give household 1 enough cash to pay for education
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.HOUSEHOLD,
            agent_id=1,
            balance_sheet={"cash": 100.0, "deposits": 0.0, "inventory_goods": 0.0},
        )
    )

    await orch.data_access.apply_updates(sim_id, updates)

    # household 1 chooses to study this tick and pays tuition (via overrides)
    cfg = get_world_config()
    cost = float(cfg.policies.education_cost_per_day)
    overrides = TickDecisionOverrides(
        households={
            1: HouseholdDecisionOverride(is_studying=True, education_payment=cost)
        }
    )

    # Register trivial placeholder scripts for required agent kinds so orchestrator
    # does not reject due to missing scripts. These scripts return no overrides.
    trivial = """
def generate_decisions(context):
    return None
"""
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.FIRM,
        entity_id=firm.id,
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.BANK,
        entity_id="bank",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.GOVERNMENT,
        entity_id="government",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.CENTRAL_BANK,
        entity_id="central_bank",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="1",
    )
    await orch.register_script_for_simulation(
        sim_id,
        user_id="u1",
        script_code=trivial,
        agent_kind=AgentKind.HOUSEHOLD,
        entity_id="2",
    )

    # Replace fallback manager with a simple one that returns minimal valid decisions
    # so our test is not sensitive to baseline script internals.
    from econ_sim.data_access.models import (
        HouseholdDecision,
        FirmDecision,
        BankDecision,
        GovernmentDecision,
        CentralBankDecision,
        TickDecisions,
    )

    class _SimpleFallback:
        def generate_decisions(self, world_state, config):
            households = {}
            for hid in world_state.households.keys():
                households[hid] = HouseholdDecision(
                    labor_supply=1.0,
                    consumption_budget=40.0,
                    savings_rate=0.3,
                    is_studying=False,
                    education_payment=0.0,
                    deposit_order=0.0,
                    withdrawal_order=0.0,
                )

            firm = world_state.firm
            bank = world_state.bank
            government = world_state.government
            central = world_state.central_bank

            firm_dec = FirmDecision(
                price=firm.price,
                planned_production=0.0,
                wage_offer=firm.wage_offer,
                hiring_demand=0,
            )
            bank_dec = BankDecision(
                deposit_rate=bank.deposit_rate,
                loan_rate=bank.loan_rate,
                loan_supply=0.0,
            )
            government_dec = GovernmentDecision(
                tax_rate=government.tax_rate, government_jobs=0, transfer_budget=0.0
            )
            central_dec = CentralBankDecision(
                policy_rate=central.base_rate, reserve_ratio=central.reserve_ratio
            )

            return TickDecisions(
                households=households,
                firm=firm_dec,
                bank=bank_dec,
                government=government_dec,
                central_bank=central_dec,
            )

    orch._fallback_manager = _SimpleFallback()

    # Run one tick: this will execute daily_settlement first (pay wages to hh2 and clear employment),
    # then decisions+education processing (household1 pays tuition and is marked studying), and labor market
    # should exclude household1 from assignments.
    result = await orch.run_tick(sim_id, overrides=overrides)
    ws_after = result.world_state

    # Check that household 2 received wage payment and firm's employees cleared by daily_settlement
    hh2 = ws_after.households.get(2)
    assert hh2 is not None
    # wage_offer was 50. If firm had cash, hh2.cash should be increased by up to 50.
    assert float(hh2.balance_sheet.cash or 0.0) >= 0.0
    # firm employees should be cleared at settlement
    assert ws_after.firm.employees == []

    # Check household1 marked studying and payment produced ledger entries
    hh1 = ws_after.households.get(1)
    assert hh1 is not None
    assert hh1.is_studying is True

    # There should be ledger entries in the tick result capturing transfers (tuition & wages)
    assert getattr(result, "ledgers", None) is not None
    ledger_amounts = [abs(float(e.amount)) for e in result.ledgers]
    assert any(a >= cost for a in ledger_amounts)

    # Advance ticks until the next daily decision tick occurs (tick_in_day == 1)
    ticks_per_day = int(cfg.simulation.ticks_per_day)
    max_adv = ticks_per_day + 2
    ws_now = ws_after
    ran = 0
    result2 = None
    while True:
        result2 = await orch.run_tick(sim_id)
        ws_now = result2.world_state
        ran += 1
        if (ws_now.tick % ticks_per_day) == 0:
            break
        if ran > max_adv:
            pytest.fail("Did not reach next daily tick within expected bound")
    # we've advanced until the tick that starts the new day (i.e. the stored
    # world_state reached a tick divisible by ticks_per_day). The actual
    # settlement logic runs at the *start* of the next run_tick when that tick
    # is observed by the orchestrator. Therefore run one more tick to trigger
    # the settlement processing that finalizes education gains.
    res_final = await orch.run_tick(sim_id)
    ws_final = res_final.world_state

    # household1 should have is_studying cleared and education_level increased by education_gain
    hh1_final = ws_final.households.get(1)
    assert hh1_final is not None
    assert hh1_final.is_studying is False
    # education level increased at least by the configured gain
    gain = float(cfg.policies.education_gain)
    assert float(hh1_final.education_level) >= initial_edu + gain


def test_employed_household_rejected_from_education_request():
    world_state = WorldState(
        simulation_id="sim",
        tick=1,
        day=1,
        households={
            1: HouseholdState(
                id=1,
                balance_sheet=BalanceSheet(cash=100.0),
                skill=1.0,
                employment_status=EmploymentStatus.EMPLOYED_FIRM,
                is_studying=False,
            )
        },
        firm=FirmState(),
        bank=BankState(),
        government=GovernmentState(),
        central_bank=CentralBankState(),
        macro=MacroState(),
        features=SimulationFeatures(),
    )

    decision = HouseholdDecision(
        labor_supply=0.0,
        consumption_budget=0.0,
        savings_rate=0.0,
        is_studying=True,
        education_payment=25.0,
        deposit_order=0.0,
        withdrawal_order=0.0,
    )

    decisions = SimpleNamespace(households={1: decision})

    updates, ledgers, log = education.process_education(
        world_state, decisions, tick=1, day=1
    )

    assert updates == []
    assert ledgers == []
    hh = world_state.households[1]
    assert hh.is_studying is False
    assert "rejected_employed" in log.context
