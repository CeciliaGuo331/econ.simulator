import pytest

from econ_sim.data_access import models
from econ_sim.logic_modules import bond_market
from econ_sim.utils.settings import get_world_config


def test_clear_bond_auction_ytm():
    cfg = get_world_config()
    ticks_per_year = int(cfg.simulation.ticks_per_day * 365)
    # create world
    ws = models.WorldState(
        simulation_id="s1",
        tick=0,
        day=0,
        households={},
        firm=None,
        bank=models.BankState(),
        government=models.GovernmentState(),
        central_bank=None,
        macro=models.MacroState(),
    )
    # set bank in world
    ws.bank.balance_sheet.cash = 100000.0
    # define bond with annual coupon: we represent coupon_rate as per-tick, so divide annual rate by ticks_per_year
    face = 100.0
    annual_coupon_rate = 0.05
    freq = ticks_per_year
    coupon_rate_per_tick = annual_coupon_rate / ticks_per_year
    maturity_tick = freq * 10
    bond = models.BondInstrument(
        id="btest",
        issuer=ws.government.id,
        face_value=face,
        coupon_rate=coupon_rate_per_tick,
        coupon_frequency_ticks=freq,
        next_coupon_tick=freq,
        maturity_tick=maturity_tick,
        outstanding=1.0,
        holders={},
    )
    # create a bid at par price
    bids = [
        {
            "buyer_kind": models.AgentKind.BANK,
            "buyer_id": ws.bank.id,
            "price": 100.0,
            "quantity": 1.0,
        }
    ]
    res = bond_market.clear_bond_auction(ws, bond, bids, tick=0, day=0)
    # market_yield should be close to annual_coupon_rate (approximately 5%)
    assert res.get("market_yield") is not None
    assert abs(res.get("market_yield") - annual_coupon_rate) < 1e-3
