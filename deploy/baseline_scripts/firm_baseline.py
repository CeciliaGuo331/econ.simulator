"""Baseline firm strategy for Docker deployments."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp
import math


Context = dict[str, object]
DecisionOverrides = dict[str, object]


def generate_decisions(context: Context) -> DecisionOverrides:
    world = context.get("world_state", {})
    macro = world.get("macro", {})
    features = world.get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))
    firm = context.get("entity_state") or world.get("firm")
    if not firm:
        return {}

    builder = OverridesBuilder()
    households = world.get("households", {})
    household_count = max(1, len(households))
    recent_consumption = sum(
        h.get("last_consumption", 0.0) for h in households.values()
    )
    # preserve a simple demand proxy for price adjustment while using Cobb-Douglas for production
    demand_proxy = max(household_count * 60.0, recent_consumption * 0.8)

    balance_sheet = firm.get("balance_sheet", {})
    inventory = balance_sheet.get("inventory_goods", 0.0)
    desired_inventory = household_count * 1.5
    inventory_gap = desired_inventory - inventory

    # Production: Cobb-Douglas Y = technology * K^alpha * L^(1-alpha)
    alpha = 0.33
    capital_stock = float(firm.get("capital_stock", 150.0))
    technology = float(firm.get("technology", 1.0))
    # approximate effective labor as number_of_employees * average productivity
    employees = firm.get("employees", []) or []
    avg_worker_prod = float(firm.get("productivity", 1.0))
    labor_input = max(1.0, len(employees) * avg_worker_prod)
    output = technology * (capital_stock**alpha) * (labor_input ** (1.0 - alpha))

    # planned_production is the Cobb-Douglas output plus an inventory gap adjustment
    planned_production = max(0.0, output + inventory_gap)

    price_adjustment = clamp(
        1.0 + inventory_gap / max(desired_inventory, 1.0) * 0.1, 0.9, 1.1
    )
    wage_adjustment = clamp(1.0 - macro.get("unemployment_rate", 0.0) * 0.1, 0.9, 1.1)

    productivity = max(avg_worker_prod, 0.1)
    required_workers = int(math.ceil(planned_production / max(1e-6, productivity)))
    # hiring and wage offers only updated on daily decision ticks
    hiring_demand = None
    wage_offer = None
    if is_daily:
        hiring_demand = max(0, required_workers - len(firm.get("employees", [])))
        wage_offer = round(firm.get("wage_offer", 80.0) * wage_adjustment, 2)

    # price and planned production are updated each tick; hiring/wage only on daily
    firm_fields: dict = {
        "planned_production": round(planned_production, 2),
        "price": round(firm["price"] * price_adjustment, 2),
    }
    if wage_offer is not None:
        firm_fields["wage_offer"] = wage_offer
    if hiring_demand is not None:
        firm_fields["hiring_demand"] = hiring_demand

    builder.firm(**firm_fields)

    return builder.build()
