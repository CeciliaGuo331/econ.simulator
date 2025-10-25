"""Tests for government transfer payments (means-tested and unemployment)."""

from econ_sim.core import entity_factory
from econ_sim.utils.settings import get_world_config
from econ_sim.logic_modules.government_transfers import (
    means_tested_transfer,
    unemployment_benefit,
)
from econ_sim.data_access.models import (
    WorldState,
    HouseholdDecision,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
    SimulationFeatures,
)


def make_world():
    cfg = get_world_config()
    households = {
        hid: entity_factory.create_household_state(cfg, hid) for hid in range(1, 11)
    }
    firm = entity_factory.create_firm_state(cfg, "firm_1")
    firm.employees = []
    bank = entity_factory.create_bank_state(cfg, "bank", households)
    government = entity_factory.create_government_state(cfg, "government")
    central_bank = entity_factory.create_central_bank_state(cfg, "central_bank")
    macro = entity_factory.create_macro_state()

    ws = WorldState(
        simulation_id="gov_test",
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


def test_means_tested_happy_path():
    ws = make_world()
    cfg = get_world_config()

    # set a few households to have low cash
    for hid in range(1, 4):
        ws.households[hid].balance_sheet.cash = 10.0

    gov_decision = GovernmentDecision(
        tax_rate=0.15, government_jobs=0, transfer_budget=100.0
    )

    updates, ledger, log = means_tested_transfer(ws, gov_decision)

    # expect updates for households and government
    assert any(u.scope == "household" for u in updates)
    assert any(u.scope == "government" for u in updates)
    assert log.context["beneficiary_count"] == 3
    assert float(log.context["total_paid"]) > 0.0


def test_unemployment_debt_funding():
    ws = make_world()
    cfg = get_world_config()

    # make all households employed first, then mark some as unemployed
    for hid in ws.households.keys():
        ws.households[hid].employment_status = ws.households[
            hid
        ].employment_status.__class__.EMPLOYED_FIRM

    for hid in range(1, 5):
        ws.households[hid].employment_status = ws.households[
            hid
        ].employment_status.__class__.UNEMPLOYED
        ws.households[hid].balance_sheet.cash = 0.0

    # set government cash to small amount and allow debt by config default
    ws.government.balance_sheet.cash = 10.0

    gov_decision = GovernmentDecision(
        tax_rate=0.15, government_jobs=0, transfer_budget=0.0
    )

    updates, ledger, log = unemployment_benefit(ws, gov_decision)

    # expect payments applied (even if via debt), and a bond_issuance ledger entry
    assert any(u.scope == "household" for u in updates)
    assert any(u.scope == "government" for u in updates)
    assert any(entry.entry_type == "bond_issuance" for entry in ledger)
    assert log.context["beneficiary_count"] == 4
