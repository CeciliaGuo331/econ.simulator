import pytest
from econ_sim.data_access.models import (
    WorldState,
    FirmState,
    BankState,
    GovernmentState,
    CentralBankState,
    HouseholdState,
    BalanceSheet,
)
from econ_sim.new_logic import central_bank as cb


def make_world_for_omo():
    from econ_sim.data_access.models import MacroState

    ws = WorldState(
        simulation_id="omo_test",
        tick=1,
        day=1,
        firm=FirmState(id="firm_1"),
        bank=BankState(id="bank"),
        government=GovernmentState(id="government"),
        central_bank=CentralBankState(id="central_bank"),
        macro=MacroState(),
    )
    ws.households[1] = HouseholdState(id=1, balance_sheet=BalanceSheet(cash=0.0))
    # give bank a bond to sell
    ws.bank.bond_holdings["bond_x"] = 10.0
    ws.bank.balance_sheet.cash = 100.0
    ws.central_bank.bond_holdings["bond_x"] = 0.0
    ws.central_bank.balance_sheet.cash = 0.0
    return ws


def test_central_bank_buys_from_bank():
    ws = make_world_for_omo()
    updates, ledgers, log = cb.process_omo(
        ws,
        tick=1,
        day=1,
        omo_ops=[{"bond_id": "bond_x", "side": "buy", "quantity": 5, "price": 2.0}],
    )

    # bank should receive cash 5*2=10
    assert ws.bank.balance_sheet.cash == pytest.approx(110.0)
    # central bank bond holdings increased
    assert ws.central_bank.bond_holdings.get("bond_x", 0.0) == pytest.approx(5.0)


def test_central_bank_sells_to_bank():
    ws = make_world_for_omo()
    # give central some bonds
    ws.central_bank.bond_holdings["bond_x"] = 8.0
    ws.bank.balance_sheet.cash = 200.0
    updates, ledgers, log = cb.process_omo(
        ws,
        tick=1,
        day=1,
        omo_ops=[{"bond_id": "bond_x", "side": "sell", "quantity": 3, "price": 5.0}],
    )

    # bank cash should decrease by 3*5=15
    assert ws.bank.balance_sheet.cash == pytest.approx(185.0)
    # central bank bond holdings decreased
    assert ws.central_bank.bond_holdings.get("bond_x", 0.0) == pytest.approx(5.0)
    # bank holdings increased by 3
    assert ws.bank.bond_holdings.get("bond_x", 0.0) == pytest.approx(13.0)
