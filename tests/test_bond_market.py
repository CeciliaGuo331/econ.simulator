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
from econ_sim.new_logic import government_financial, central_bank_policy


def make_world():
    from econ_sim.data_access.models import MacroState

    ws = WorldState(
        simulation_id="sim1",
        tick=1,
        day=1,
        firm=FirmState(id="firm_1"),
        bank=BankState(id="bank"),
        government=GovernmentState(id="government"),
        central_bank=CentralBankState(id="central_bank"),
        macro=MacroState(),
    )
    # add one household
    ws.households[1] = HouseholdState(id=1, balance_sheet=BalanceSheet(cash=1000.0))
    # set bank cash
    ws.bank.balance_sheet.cash = 5000.0
    ws.government.balance_sheet.cash = 100.0
    ws.central_bank.balance_sheet.cash = 10000.0
    return ws


def test_bond_issuance_and_omo():
    ws = make_world()

    # bids: bank bids at price 100 for 50 units; household bids price 95 for 20 units
    bids = [
        {
            "buyer_kind": "bank",
            "buyer_id": ws.bank.id,
            "price": 100.0,
            "quantity": 50.0,
        },
        {"buyer_kind": "household", "buyer_id": 1, "price": 95.0, "quantity": 20.0},
    ]

    res = government_financial.issue_bonds(
        ws,
        face_value=100.0,
        coupon_rate=5.0,
        maturity_tick=10,
        volume=60.0,
        bids=bids,
        tick=1,
        day=1,
    )

    bond = res.get("bond")
    assert bond is not None
    # market_price should be between bids (weighted by clearing)
    assert res.get("market_price") is not None
    assert ws.government.debt_instruments.get(bond.id) is not None

    # bank should have purchased at least some quantity
    assert ws.bank.bond_holdings.get(bond.id, 0.0) > 0.0

    # government cash increased
    assert ws.government.balance_sheet.cash > 100.0

    # Now central bank performs OMO: buy up to bank's holdings
    cb_res = central_bank_policy.open_market_operation(
        ws,
        bond_id=bond.id,
        quantity=30.0,
        side="buy",
        price=res.get("market_price"),
        tick=1,
        day=1,
    )

    assert cb_res.get("transacted_quantity", 0) > 0
    assert ws.central_bank.bond_holdings.get(bond.id, 0.0) > 0.0
    assert ws.bank.balance_sheet.cash > 0.0
