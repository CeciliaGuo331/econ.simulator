import math

from econ_sim.data_access.models import (
    WorldState,
    HouseholdState,
    BalanceSheet,
    FirmState,
    MacroState,
    HouseholdDecision,
    TickDecisions,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)
from econ_sim.logic_modules.goods_market import clear_goods_market_new
from econ_sim.logic_modules.utility import accumulate_utility
from econ_sim.utils.settings import get_world_config


def build_minimal_world():
    cfg = get_world_config()
    world = WorldState(
        simulation_id="test",
        tick=1,
        day=1,
        households={},
        firm=FirmState(),
        bank=None,
        government=None,
        central_bank=None,
        macro=MacroState(),
    )
    return world


def test_goods_market_writes_last_consumption_and_utility():
    world = build_minimal_world()

    # set up a firm with inventory and price
    world.firm.balance_sheet.inventory_goods = 10.0
    world.firm.price = 10.0

    # create a household with very low explicit consumption_budget (0)
    hh = HouseholdState(id=0, balance_sheet=BalanceSheet(cash=100.0, deposits=0.0))
    world.households[0] = hh

    # decisions: household intends to consume 0 (edge case)
    hd = HouseholdDecision(labor_supply=1.0, consumption_budget=0.0, savings_rate=0.1)

    decisions = TickDecisions(
        households={0: hd},
        firm=FirmDecision(
            price=world.firm.price,
            planned_production=0.0,
            wage_offer=80.0,
            hiring_demand=0,
        ),
        bank=BankDecision(deposit_rate=0.01, loan_rate=0.05, loan_supply=0.0),
        government=GovernmentDecision(
            tax_rate=0.15, government_jobs=0, transfer_budget=0.0
        ),
        central_bank=CentralBankDecision(policy_rate=0.03, reserve_ratio=0.1),
    )

    updates, log = clear_goods_market_new(world, decisions)

    # subsistence from config
    subsistence = float(get_world_config().markets.goods.subsistence_consumption)

    # After clearing, the in-memory world_state should have last_consumption > 0
    assert hasattr(world.households[0], "last_consumption")
    assert world.households[0].last_consumption >= subsistence

    # Now accumulate utility and inspect the produced update
    util_updates, util_log = accumulate_utility(world, tick=1, day=1)
    # find household update
    hh_update = None
    for u in util_updates:
        if u.scope == "household" and str(u.agent_id) == "0":
            hh_update = u
            break

    assert hh_update is not None, "utility module must produce an update for household"
    # the last_instant_utility should be >= log(subsistence) (log(1)=0) so non-negative
    last_u = float(hh_update.changes.get("last_instant_utility", -9999))
    assert last_u >= math.log(max(1e-9, subsistence))
