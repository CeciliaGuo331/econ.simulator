"""Tests for the new labor market subsystem."""

from econ_sim.core import entity_factory
from econ_sim.utils.settings import get_world_config
from econ_sim.logic_modules.labor_market import resolve_labor_market_new
from econ_sim.data_access.models import (
    TickDecisions,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
    SimulationFeatures,
    WorldState,
)


def make_world():
    cfg = get_world_config()
    households = {
        hid: entity_factory.create_household_state(cfg, hid) for hid in range(1, 11)
    }
    firm = entity_factory.create_firm_state(cfg, "firm_1")
    # start with no employees
    firm.employees = []
    bank = entity_factory.create_bank_state(cfg, "bank", households)
    government = entity_factory.create_government_state(cfg, "government")
    central_bank = entity_factory.create_central_bank_state(cfg, "central_bank")
    macro = entity_factory.create_macro_state()

    ws = WorldState(
        simulation_id="lab_test",
        tick=0,
        day=0,
        households=households,
        firm=firm,
        bank=bank,
        government=government,
        central_bank=central_bank,
        macro=macro,
        features=SimulationFeatures(),
    )
    return ws


def test_labor_matching_basic():
    ws = make_world()

    # All households signal they want to work
    households_decisions = {
        hid: HouseholdDecision(
            labor_supply=1.0, consumption_budget=1.0, savings_rate=0.1
        )
        for hid in ws.households.keys()
    }
    # Firm wants to hire 3
    firm_decision = FirmDecision(
        price=ws.firm.price, planned_production=0.0, wage_offer=100.0, hiring_demand=3
    )
    bank_decision = BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0)
    government_decision = GovernmentDecision(
        tax_rate=0.15, government_jobs=2, transfer_budget=0.0
    )
    cb_decision = CentralBankDecision(policy_rate=0.02, reserve_ratio=0.08)

    decisions = TickDecisions(
        households=households_decisions,
        firm=firm_decision,
        bank=bank_decision,
        government=government_decision,
        central_bank=cb_decision,
    )

    updates, log = resolve_labor_market_new(ws, decisions)
    # expect updates include firm employees and household employment updates
    assert any(u.scope == "firm" for u in updates)
    assert any(u.scope == "household" for u in updates)
    # log should contain assigned lists
    assert "assigned_firm" in log.context
    assert "assigned_government" in log.context
