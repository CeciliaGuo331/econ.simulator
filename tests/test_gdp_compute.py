import pytest

from types import SimpleNamespace

from econ_sim.logic_modules.goods_market import compute_gdp_from_production


def make_world(firm_prod=None, firm_price=None, gov_spending=None):
    firm = SimpleNamespace()
    if firm_prod is not None:
        firm.last_production = firm_prod
    else:
        # ensure attribute absence case
        pass
    if firm_price is not None:
        firm.price = firm_price

    government = SimpleNamespace()
    if gov_spending is not None:
        government.spending = gov_spending

    world = SimpleNamespace()
    world.firm = firm
    world.government = government
    return world


def test_compute_gdp_uses_production_when_available():
    world = make_world(firm_prod=50.0, firm_price=3.0, gov_spending=7.0)
    gdp = compute_gdp_from_production(world, ask_price=3.0, goods_sold=0.0)
    assert gdp == pytest.approx(3.0 * 50.0 + 7.0)


def test_compute_gdp_falls_back_to_goods_sold():
    world = make_world(firm_prod=None, firm_price=2.0, gov_spending=5.0)
    gdp = compute_gdp_from_production(world, ask_price=2.0, goods_sold=12.0)
    assert gdp == pytest.approx(2.0 * 12.0 + 5.0)
