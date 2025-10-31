#!/usr/bin/env python3
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
from econ_sim.logic_modules import baseline_stub


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
        simulation_id="debug",
        tick=1,
        day=1,
        households=households,
        firm=firm,
        bank=bank,
        government=gov,
        central_bank=cb,
        macro=macro,
    )


if __name__ == "__main__":
    w = build_sample_world(6)
    decisions = baseline_stub.generate_baseline_decisions(w)
    print("Baseline decisions sample (household consumption budgets):")
    for hid, d in decisions.households.items():
        print(hid, getattr(d, "consumption_budget", None))

    updates, logs, ledgers, signals = run_tick_new(w)
    print("\nLOGS:")
    for l in logs:
        print(l.message, l.context)

    print("\nUPDATES (household last_consumption if present):")
    found = False
    for u in updates:
        ctx = getattr(u, "changes", {}) or {}
        if "last_consumption" in ctx:
            print(u.scope, u.agent_id, ctx.get("last_consumption"))
            found = True
    if not found:
        print("No last_consumption in updates")

    print("\nFinal in-memory last_consumption for households:")
    for hid, hh in w.households.items():
        print(hid, hh.last_consumption)

    print("\nDone")
