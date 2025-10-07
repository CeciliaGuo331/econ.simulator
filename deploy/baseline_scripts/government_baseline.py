"""Baseline government strategy for Docker deployments."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


Context = dict[str, object]
DecisionOverrides = dict[str, object]


def generate_decisions(context: Context) -> DecisionOverrides:
    world = context.get("world_state", {})
    macro = world.get("macro", {})
    government = context.get("entity_state") or world.get("government")

    if not government:
        return {}

    builder = OverridesBuilder()

    unemployment_gap = max(0.0, macro.get("unemployment_rate", 0.0) - 0.06)
    households = len(world.get("households", {}))
    base_tax = government.get("tax_rate", 0.15)

    tax_rate = clamp(base_tax - unemployment_gap * 0.1, 0.05, 0.45)
    government_jobs = max(
        len(government.get("employees", [])),
        int(households * unemployment_gap * 0.4),
    )
    transfer_budget = max(
        0.0,
        households
        * government.get("unemployment_benefit", 50.0)
        * unemployment_gap
        * 50,
    )

    builder.government(
        tax_rate=round(tax_rate, 4),
        government_jobs=government_jobs,
        transfer_budget=round(transfer_budget, 2),
    )

    return builder.build()
