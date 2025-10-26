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
    # fiscal rule: countercyclical tax adjustments and transfer budgeting
    tax_rate = clamp(base_tax - unemployment_gap * 0.1, 0.05, 0.45)

    # government jobs scale with unemployment gap and base headcount
    government_jobs = max(
        len(government.get("employees", [])),
        int(households * unemployment_gap * 0.4),
    )

    # transfer budget: simple rule from docs (transfer_payment = 5 * unemployed_households)
    unemployed_share = macro.get("unemployment_rate", 0.0)
    unemployed_households = int(households * unemployed_share)
    transfer_budget = round(5.0 * unemployed_households, 2)

    # compute issuance plan if fiscal balance negative
    gdp = macro.get("gdp", 0.0)
    tax_revenue = tax_rate * max(gdp, 0.0)
    spending = government.get("spending", 10000.0)
    fiscal_balance = tax_revenue - spending - transfer_budget
    issuance_volume = 0.0
    issuance_plan = None
    if fiscal_balance < 0:
        issuance_volume = round(-fiscal_balance, 2)
        # propose issuance with a coupon slightly above policy rate
        min_price = None
        issuance_plan = {"volume": issuance_volume, "min_price": None}

    builder.government(
        tax_rate=round(tax_rate, 4),
        government_jobs=government_jobs,
        transfer_budget=round(transfer_budget, 2),
        issuance_plan=issuance_plan,
    )

    return builder.build()
