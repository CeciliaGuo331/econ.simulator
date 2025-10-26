from econ_sim.data_access.models import (
    WorldState,
    MacroState,
    GovernmentState,
    BankState,
    HouseholdState,
    BalanceSheet,
)
from econ_sim.logic_modules import government_financial


def make_world():
    gov = GovernmentState(id="government", balance_sheet=BalanceSheet(cash=0.0))
    bank = BankState(id="bank", balance_sheet=BalanceSheet(cash=1000.0))
    hh = HouseholdState(id=1, balance_sheet=BalanceSheet(cash=500.0))
    ws = WorldState(
        simulation_id="s_test",
        tick=10,
        day=1,
        households={1: hh},
        firm=None,
        bank=bank,
        government=gov,
        central_bank=None,
        macro=MacroState(),
    )
    return ws


def test_issue_bonds_with_issuance_plan_min_price():
    ws = make_world()

    # bids: bank bids below reserve, household bids at reserve
    bids = [
        {"buyer_kind": "bank", "buyer_id": ws.bank.id, "price": 0.9, "quantity": 100},
        {"buyer_kind": "household", "buyer_id": 1, "price": 1.0, "quantity": 100},
    ]

    issuance_plan = {"volume": 150, "min_price": 1.0}

    res = government_financial.issue_bonds(
        ws,
        face_value=1.0,
        coupon_rate=0.0,
        maturity_tick=ws.tick + 10,
        volume=150,
        bids=bids,
        tick=ws.tick,
        day=ws.day,
        issuance_plan=issuance_plan,
    )

    bond = res.get("bond")
    assert bond is not None
    # purchase records should only include household (bank bid below min_price filtered)
    buyers = {rec.get("buyer_id") for rec in bond.purchase_records}
    assert str(1) in buyers
    assert ws.bank.balance_sheet.cash == 1000.0  # bank didn't spend
    # government cash increased by household payment (100 * 1.0)
    assert float(ws.government.balance_sheet.cash) == 100.0
