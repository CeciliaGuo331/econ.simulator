from econ_sim.utils.settings import get_world_config
from econ_sim.core.entity_factory import (
    create_household_state,
    create_firm_state,
    create_bank_state,
    create_government_state,
    create_central_bank_state,
    create_macro_state,
)
from econ_sim.logic_modules import baseline_stub
from econ_sim.core.orchestrator import run_tick_new


def build_sample_world(num_households: int = 6):
    cfg = get_world_config()
    households = {}
    for hid in range(num_households):
        households[hid] = create_household_state(cfg, hid)

    firm = create_firm_state(cfg, "firm_1")
    bank = create_bank_state(cfg, "bank", households)
    gov = create_government_state(cfg, "government")
    cb = create_central_bank_state(cfg, "central_bank")
    macro = create_macro_state()

    from econ_sim.data_access.models import WorldState

    return WorldState(
        simulation_id="diag",
        tick=0,
        day=1,
        households=households,
        firm=firm,
        bank=bank,
        government=gov,
        central_bank=cb,
        macro=macro,
    )


if __name__ == "__main__":
    world = build_sample_world(6)
    print("-- Initial household seeds (world_state.households[].is_studying) --")
    for hid, hh in world.households.items():
        print(
            f"hh {hid}: seeded_is_studying={hh.is_studying}, cash={hh.balance_sheet.cash:.2f}, deposits={hh.balance_sheet.deposits:.2f}"
        )

    print("\n-- Baseline decisions per household --")
    decisions = baseline_stub.generate_baseline_decisions(world)
    try:
        firm_wage = float(world.firm.wage_offer)
    except Exception:
        firm_wage = 0.0

    for hid, dec in decisions.households.items():
        try:
            h = world.households[hid]
            assets = float(
                (h.balance_sheet.cash or 0.0) + (h.balance_sheet.deposits or 0.0)
            )
        except Exception:
            assets = 0.0
        expected_wage_gain = (
            firm_wage * (0.6 * float(getattr(dec, "education_payment", 0.0)))
            if firm_wage
            else 0.0
        )
        print(
            f"hh {hid}: assets={assets:.2f}, is_studying_decision={dec.is_studying}, education_payment={dec.education_payment}, consumption_budget={dec.consumption_budget}"
        )

    print("\n-- Run one tick (run_tick_new) and print tick logs --")
    updates, logs, ledgers, signals = run_tick_new(world)
    for l in logs:
        try:
            print(l.message, l.context)
        except Exception:
            print(repr(l))

    print("\n-- Persisted updates (summary) --")
    for u in updates:
        try:
            print(f"scope={u.scope} agent_id={u.agent_id} changes={u.changes}")
        except Exception:
            print(repr(u))
