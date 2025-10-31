from econ_sim.core.orchestrator import _execute_market_logic
from econ_sim.data_access.models import (
    WorldState,
    HouseholdState,
    BalanceSheet,
    FirmState,
    BankState,
    GovernmentState,
    CentralBankState,
    MacroState,
    HouseholdDecision,
    TickDecisions,
    FirmDecision,
    BankDecision,
    GovernmentDecision,
    CentralBankDecision,
)
from econ_sim.utils.settings import get_world_config


def test_market_tick_consumption_and_education_applied():
    cfg = get_world_config()
    # build minimal world
    world = WorldState(
        simulation_id="test",
        tick=1,
        day=1,
        households={},
        firm=FirmState(),
        bank=BankState(),
        government=GovernmentState(),
        central_bank=CentralBankState(),
        macro=MacroState(),
    )

    # set firm inventory and price
    world.firm.balance_sheet.inventory_goods = 10.0
    world.firm.price = 10.0

    # add household with enough cash
    hh = HouseholdState(id=0, balance_sheet=BalanceSheet(cash=100.0, deposits=0.0))
    world.households[0] = hh

    # build decisions: household wants to consume and study
    hh_dec = HouseholdDecision(
        labor_supply=1.0,
        consumption_budget=20.0,
        savings_rate=0.1,
        is_studying=True,
        education_payment=2.0,
    )

    decisions = TickDecisions(
        households={0: hh_dec},
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

    # Call goods_market directly first to capture exceptions if any
    from econ_sim.logic_modules import goods_market

    try:
        g_updates, g_log = goods_market.clear_goods_market_new(world, decisions)
    except Exception as exc:
        raise AssertionError(
            f"goods_market.clear_goods_market_new raised: {exc}"
        ) from exc

    assert (
        g_log is not None
        and getattr(g_log, "message", None) == "goods_market_cleared_new"
    )

    # Now run full market logic and verify education processed and utility
    updates, logs, ledgers, signals = _execute_market_logic(world, decisions, cfg, {})
    messages = [l.message for l in logs if hasattr(l, "message")]
    assert any(
        "education_processed" == m for m in messages
    ), f"education_processed missing, logs={messages}"

    # in-memory world_state should reflect last_consumption and is_studying
    assert getattr(world.households[0], "last_consumption", 0.0) > 0.0
    assert getattr(world.households[0], "is_studying", False) is True


from econ_sim.core.entity_factory import (
    create_household_state,
    create_firm_state,
    create_bank_state,
    create_government_state,
    create_central_bank_state,
    create_macro_state,
)
from econ_sim.utils.settings import get_world_config
from econ_sim.data_access.models import WorldState
from econ_sim.core.orchestrator import run_tick_new
from econ_sim.logic_modules import goods_market


def build_sample_world(num_households: int = 6) -> WorldState:
    cfg = get_world_config()
    households = {}
    for hid in range(num_households):
        households[hid] = create_household_state(cfg, hid)

    firm = create_firm_state(cfg, "firm_1")
    bank = create_bank_state(cfg, "bank", households)
    gov = create_government_state(cfg, "government")
    cb = create_central_bank_state(cfg, "central_bank")
    macro = create_macro_state()

    return WorldState(
        simulation_id="test",
        tick=1,
        day=1,
        households=households,
        firm=firm,
        bank=bank,
        government=gov,
        central_bank=cb,
        macro=macro,
    )


def test_run_tick_produces_consumption_and_education():
    world = build_sample_world(6)
    # First call goods_market directly with baseline decisions to see if it raises an exception
    try:
        from econ_sim.logic_modules import baseline_stub

        decisions = baseline_stub.generate_baseline_decisions(world)
        g_updates, g_log = goods_market.clear_goods_market_new(world, decisions)
    except Exception as exc:
        raise AssertionError(f"goods_market.clear_goods_market_new raised: {exc}")

    assert g_log is not None and g_log.message == "goods_market_cleared_new"

    # now run full tick to ensure end-to-end integration
    updates, logs, ledgers, signals = run_tick_new(world)

    util_logs = [l for l in logs if l.message == "utility_accumulated"]
    assert util_logs, "utility_accumulated log expected"

    # some households should have last_consumption > 0 in updates
    found = False
    for u in updates:
        ctx = getattr(u, "changes", {}) or {}
        if "last_consumption" in ctx and float(ctx.get("last_consumption", 0.0)) > 0.0:
            found = True
            break

    assert (
        found
    ), "At least one household should have positive last_consumption after goods market"
