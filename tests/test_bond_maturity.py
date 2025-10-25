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
from econ_sim.logic_modules import government_financial, government_financial as gf


def make_world_one_holder():
    from econ_sim.data_access.models import MacroState

    ws = WorldState(
        simulation_id="maturity_test",
        tick=10,
        day=1,
        firm=FirmState(id="firm_1"),
        bank=BankState(id="bank"),
        government=GovernmentState(id="government"),
        central_bank=CentralBankState(id="central_bank"),
        macro=MacroState(),
    )
    ws.households[1] = HouseholdState(id=1, balance_sheet=BalanceSheet(cash=0.0))
    # give bank some cash for purchases
    ws.bank.balance_sheet.cash = 1000.0
    ws.government.balance_sheet.cash = 1000.0
    return ws


def test_bond_matures_and_pays_holders():
    ws = make_world_one_holder()
    tick = ws.tick
    # create a bond maturing now with face_value=100, coupon_rate=0.05, outstanding 100
    bond = {
        "id": "b1",
        "issuer": ws.government.id,
        # use face_value=1 so outstanding units translate directly to payment
        "face_value": 1.0,
        "coupon_rate": 0.05,
        "maturity_tick": tick,
        "outstanding": 100.0,
        "holders": {},
    }

    # assign whole bond to bank
    from econ_sim.data_access.models import BondInstrument

    bi = BondInstrument(**bond)
    ws.government.debt_instruments[bi.id] = bi
    ws.government.debt_outstanding[bi.id] = bi.outstanding
    ws.bank.bond_holdings[bi.id] = bi.outstanding

    # set bank cash low so we can observe increase after maturity
    ws.bank.balance_sheet.cash = 0.0

    updates, ledgers, log = government_financial.process_bond_maturities(
        ws, tick=tick, day=1
    )

    # bank should receive principal+coupon: 100*(1+0.05) = 105
    assert ws.bank.balance_sheet.cash == pytest.approx(105.0)
    # government cash decreased accordingly
    assert ws.government.balance_sheet.cash == pytest.approx(1000.0 - 105.0)
    # bond removed from government's registry
    assert bi.id not in ws.government.debt_instruments
    # check ledgers include maturity payments
    assert any(entry.entry_type == "bond_maturity_receipt" for entry in ledgers)
