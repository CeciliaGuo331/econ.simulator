import pytest

from econ_sim.data_access import models
from econ_sim.logic_modules import government_financial


def make_world():
    macro = models.MacroState()
    gov = models.GovernmentState()
    bank = models.BankState()
    hh = models.HouseholdState(id=1, balance_sheet=models.BalanceSheet(cash=0.0))
    bank.balance_sheet.cash = 1000.0
    gov.balance_sheet.cash = 1000.0
    # register holders
    bank.bond_holdings = {}
    hh.bond_holdings = {}
    world = models.WorldState(
        simulation_id="s1",
        tick=0,
        day=0,
        households={1: hh},
        firm=None,
        bank=bank,
        government=gov,
        central_bank=None,
        macro=macro,
    )
    return world


def test_coupon_full_and_partial_payment():
    ws = make_world()
    tick = 1
    day = 0
    # create bond held by bank (0.5) and household (0.5)
    bond = models.BondInstrument(
        id="b1",
        issuer=ws.government.id,
        face_value=100.0,
        coupon_rate=0.10,  # annual 10%
        coupon_frequency_ticks=1,
        next_coupon_tick=1,
        maturity_tick=10,
        outstanding=1.0,
        holders={"bank": 0.5, "1": 0.5},
    )
    ws.government.debt_instruments["b1"] = bond
    ws.bank.bond_holdings["b1"] = 0.5
    ws.households[1].bond_holdings["b1"] = 0.5

    # case 1: government has enough cash
    ws.government.balance_sheet.cash = 1000.0
    updates, ledgers, log = government_financial.process_coupon_payments(
        ws, tick=tick, day=day
    )
    # ledger entries should include receipts for bank and household and government payment
    assert any(l.entry_type == "coupon_receipt" for l in ledgers)
    assert any(l.entry_type == "coupon_payment" for l in ledgers)

    # case 2: insufficient cash -> partial payments
    ws2 = make_world()
    # create a fresh bond instance for the second world to avoid mutated next_coupon_tick
    bond2 = models.BondInstrument(
        id="b1",
        issuer=ws2.government.id,
        face_value=100.0,
        coupon_rate=0.10,
        coupon_frequency_ticks=1,
        next_coupon_tick=1,
        maturity_tick=10,
        outstanding=1.0,
        holders={"bank": 0.5, "1": 0.5},
    )
    ws2.government.debt_instruments["b1"] = bond2
    ws2.bank.bond_holdings["b1"] = 0.5
    ws2.households[1].bond_holdings["b1"] = 0.5
    ws2.government.balance_sheet.cash = 0.0  # force partial (no cash)
    updates2, ledgers2, log2 = government_financial.process_coupon_payments(
        ws2, tick=tick, day=day
    )
    # should record partial receipts
    assert any("partial" in l.entry_type for l in ledgers2)
