"""Baseline firm strategy for Docker deployments."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


Context = dict[str, object]
DecisionOverrides = dict[str, object]


def generate_decisions(context: Context) -> DecisionOverrides:
    world = context["world_state"]
    macro = world["macro"]
    firm = world["firm"]

    builder = OverridesBuilder()
    households = world["households"]
    household_count = max(1, len(households))
    recent_consumption = sum(
        h.get("last_consumption", 0.0) for h in households.values()
    )
    demand_proxy = max(household_count * 60.0, recent_consumption * 0.8)

    inventory = firm["balance_sheet"].get("inventory_goods", 0.0)
    desired_inventory = household_count * 1.5
    inventory_gap = desired_inventory - inventory
    planned_production = max(0.0, demand_proxy * 0.5 + inventory_gap)

    price_adjustment = clamp(
        1.0 + inventory_gap / max(desired_inventory, 1.0) * 0.1, 0.9, 1.1
    )
    wage_adjustment = clamp(1.0 - macro.get("unemployment_rate", 0.0) * 0.1, 0.9, 1.1)

    required_workers = int(planned_production / max(firm.get("productivity", 0.1), 0.1))
    hiring_demand = max(0, required_workers - len(firm.get("employees", [])))

    builder.firm(
        planned_production=round(planned_production, 2),
        price=round(firm["price"] * price_adjustment, 2),
        wage_offer=round(firm.get("wage_offer", 80.0) * wage_adjustment, 2),
        hiring_demand=hiring_demand,
    )

    return builder.build()
