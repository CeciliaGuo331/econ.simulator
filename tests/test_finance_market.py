import pytest

from econ_sim.data_access.models import (
    WorldState,
    HouseholdState,
    BalanceSheet,
    BankState,
    FirmState,
    GovernmentState,
    MacroState,
    AgentKind,
)
from econ_sim.logic_modules import finance_market


def make_world():
    ws = WorldState(
        simulation_id="fin_test",
        tick=0,
        day=0,
        households={},
        firm=FirmState(id="firm_1"),
        bank=BankState(id="bank"),
        government=GovernmentState(id="government"),
        central_bank=None,
        macro=MacroState(),
    )
    # one household
    ws.households[1] = HouseholdState(
        id=1, balance_sheet=BalanceSheet(cash=500.0, deposits=100.0)
    )
    # set bank initial deposits to match households
    ws.bank.balance_sheet.deposits = 100.0
    return ws


def test_deposit_withdraw_consistency():
    ws = make_world()
    # deposit 50 from household to bank
    updates, ledgers, log = finance_market.deposit(
        ws, household_id=1, bank_id=ws.bank.id, amount=50.0, tick=ws.tick, day=ws.day
    )
    # after deposit, household cash decreased, deposits increased
    hh = ws.households[1]
    assert hh.balance_sheet.cash == pytest.approx(450.0)
    assert hh.balance_sheet.deposits == pytest.approx(150.0)
    # bank deposits increased
    assert ws.bank.balance_sheet.deposits == pytest.approx(150.0)

    # withdraw 20
    updates, ledgers, log = finance_market.withdraw(
        ws, household_id=1, bank_id=ws.bank.id, amount=20.0, tick=ws.tick, day=ws.day
    )
    assert hh.balance_sheet.deposits == pytest.approx(130.0)
    assert hh.balance_sheet.cash == pytest.approx(470.0)
    assert ws.bank.balance_sheet.deposits == pytest.approx(130.0)


def test_transfer_updates_balances_and_ledgers():
    ws = make_world()
    # transfer 200 from household to firm
    updates, ledgers, log = finance_market.transfer(
        ws,
        payer_kind=AgentKind.HOUSEHOLD,
        payer_id="1",
        payee_kind=AgentKind.FIRM,
        payee_id=ws.firm.id,
        amount=200.0,
        tick=ws.tick,
        day=ws.day,
    )
    # household had 500 cash, 100 deposits; after transfer cash decreases by 200
    hh = ws.households[1]
    assert hh.balance_sheet.cash == pytest.approx(300.0)
    # firm cash increased accordingly
    assert ws.firm.balance_sheet.cash == pytest.approx(200.0)
    assert any(entry.entry_type == "transfer_out" for entry in ledgers)
