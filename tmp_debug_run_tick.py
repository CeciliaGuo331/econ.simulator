from econ_sim.core.orchestrator import run_tick_new
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


from econ_sim.logic_modules import (
    labor_market,
    finance_market,
    firm_production,
    goods_market,
    baseline_stub,
)

world = build_sample_world(6)
decisions = baseline_stub.generate_baseline_decisions(world)

# Run labor market
try:
    l_updates, l_log = labor_market.resolve_labor_market_new(world, decisions)
    print("LABOR OK", l_log.message)
except Exception as e:
    print("LABOR EXC", e)

# Wages settlement
try:
    # mimic the wages settlement loop from orchestrator
    tick = world.tick
    day = world.day
    if world.firm is not None:
        for hid in getattr(world.firm, "employees", []):
            try:
                wage = float(decisions.firm.wage_offer)
                t_updates, t_ledgers, t_log = finance_market.transfer(
                    world,
                    payer_kind=finance_market.__name__ and None,
                    payer_id=world.firm.id,
                    payee_kind=None,
                    payee_id=str(hid),
                    amount=wage,
                    tick=tick,
                    day=day,
                )
            except Exception:
                pass
except Exception as e:
    print("WAGES EXC", e)

# Run firm production
try:
    p_updates, p_log = firm_production.process_production(
        world, decisions, tick=world.tick, day=world.day
    )
    print("PROD OK", p_log.message)
except Exception as e:
    print("PROD EXC", e)

# Now call goods_market and show detailed exception if it fails
try:
    g_updates, g_log = goods_market.clear_goods_market_new(world, decisions)
    print("GOODS OK", g_log.message)
except Exception as e:
    import traceback

    print("GOODS EXC", e)
    traceback.print_exc()

updates, logs, ledgers, signals = run_tick_new(world)
print("--- LOGS ---")
for l in logs:
    try:
        print(l.message, l.context)
    except Exception:
        print(repr(l))
print("\n--- UPDATES ---")
for u in updates:
    try:
        print(
            "scope=",
            u.scope,
            "agent_id=",
            u.agent_id,
            "changes=",
            u.changes,
            "mode=",
            u.mode,
        )
    except Exception:
        print(repr(u))

print("\n--- LEDGERS ---")
for ld in ledgers[:10]:
    try:
        print(ld)
    except Exception:
        print(repr(ld))

print("\n--- SIGNALS ---")
print(signals)

print("\n--- DIRECT _execute_market_logic CALL (with try/except) ---")
from econ_sim.core.orchestrator import _execute_market_logic

try:
    u2, l2, led2, s2 = _execute_market_logic(world, decisions, get_world_config(), {})
    print("EXECUTE OK, logs:")
    for ln in l2:
        print(ln.message)
except Exception as e:
    import traceback

    print("EXECUTE EXC", e)
    traceback.print_exc()
