"""Unit tests for the new goods market subsystem."""

from econ_sim.core import entity_factory
from econ_sim.utils.settings import get_world_config
from econ_sim.new_logic.goods_market import clear_goods_market_new
from econ_sim.data_access.models import (
    TickDecisions,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)


def make_world_for_goods_test():
    cfg = get_world_config()
    households = {
        hid: entity_factory.create_household_state(cfg, hid) for hid in range(1, 6)
    }
    firm = entity_factory.create_firm_state(cfg, "firm_1")
    # give the firm a small inventory
    firm.balance_sheet.inventory_goods = 10.0
    bank = entity_factory.create_bank_state(cfg, "bank", households)
    government = entity_factory.create_government_state(cfg, "government")
    central_bank = entity_factory.create_central_bank_state(cfg, "central_bank")
    world = entity_factory.create_simulation if False else None

    from econ_sim.data_access.models import WorldState, MacroState, SimulationFeatures

    ws = WorldState(
        simulation_id="gm_test",
        tick=0,
        day=0,
        households=households,
        firm=firm,
        bank=bank,
        government=government,
        central_bank=central_bank,
        macro=entity_factory.create_macro_state(),
        features=SimulationFeatures(),
    )
    return ws


def test_clear_goods_market_basic():
    ws = make_world_for_goods_test()

    # Create simple decisions: each household plans to consume goods worth 5 units
    households_decisions = {
        hid: HouseholdDecision(
            labor_supply=0.0, consumption_budget=5.0, savings_rate=0.1
        )
        for hid in ws.households.keys()
    }
    firm_decision = FirmDecision(
        price=ws.firm.price,
        planned_production=0.0,
        wage_offer=ws.firm.wage_offer,
        hiring_demand=0,
    )
    bank_decision = BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0)
    government_decision = GovernmentDecision(
        tax_rate=0.15, government_jobs=0, transfer_budget=0.0
    )
    cb_decision = CentralBankDecision(policy_rate=0.02, reserve_ratio=0.08)

    decisions = TickDecisions(
        households=households_decisions,
        firm=firm_decision,
        bank=bank_decision,
        government=government_decision,
        central_bank=cb_decision,
    )

    updates, log = clear_goods_market_new(ws, decisions)

    assert isinstance(updates, list)
    assert any(u.scope == "macro" or u.scope == "macro" for u in updates)
    assert log.message == "goods_market_cleared_new"
    # total planned demand = 5 * 5 / price; goods_sold <= inventory (10)
    assert float(log.context["goods_sold"]) <= 10.0
