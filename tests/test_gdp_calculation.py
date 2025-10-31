import asyncio
import os

import pytest


from types import SimpleNamespace

from econ_sim.logic_modules.goods_market import compute_gdp_from_production


def test_gdp_includes_government_spending():
    """Simpler test: directly simulate a world_state with a firm that has
    last_production and price, and a government with spending. Assert exact
    GDP equality using the production method.
    """
    firm = SimpleNamespace()
    firm.last_production = 123.45
    firm.price = 2.5
    government = SimpleNamespace()
    government.spending = 10.0
    world = SimpleNamespace()
    world.firm = firm
    world.government = government

    expected = firm.price * firm.last_production + government.spending
    computed = compute_gdp_from_production(world, ask_price=firm.price, goods_sold=0.0)
    assert computed == pytest.approx(expected, rel=1e-9)
