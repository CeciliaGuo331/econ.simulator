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

    # Planned production is a firm decision driven by demand; backend/production
    # module should compute realized output from inputs (K, L, technology).
    # Here we compute planned_production from demand while deriving the
    # labour needed to meet that plan using the Cobb-Douglas inverse.
    alpha = 0.33
    capital_stock = float(firm.get("capital_stock", 150.0))
    technology = float(firm.get("technology", 1.0))
    employees = firm.get("employees", []) or []
    avg_worker_prod = float(firm.get("productivity", 1.0))

    # use demand proxy to set a production target (decision variable)
    planned_from_demand = max(0.0, demand_proxy * 0.5 + inventory_gap)

    # compute theoretical labour (effective units) required to achieve planned output
    # using inverse of Cobb-Douglas: L_required = (planned / (tech * K^alpha))^(1/(1-alpha))
    denom = max(1e-9, technology * (capital_stock**alpha))
    try:
        labour_required_effective = (planned_from_demand / denom) ** (
            1.0 / (1.0 - alpha)
        )
    except Exception:
        labour_required_effective = 0.0

    # convert effective labour to headcount using average worker productivity
    required_workers = int(
        math.ceil(labour_required_effective / max(1e-6, avg_worker_prod))
    )

    # planned_production remains a decision (target); actual production should be
    # computed by the production/market engine from K, labor_assignment and tech.
    planned_production = planned_from_demand

    price_adjustment = clamp(
        1.0 + inventory_gap / max(desired_inventory, 1.0) * 0.1, 0.9, 1.1
    )
    wage_adjustment = clamp(1.0 - macro.get("unemployment_rate", 0.0) * 0.1, 0.9, 1.1)

    productivity = max(avg_worker_prod, 0.1)
    required_workers = int(math.ceil(planned_production / max(1e-6, productivity)))
    # hiring and wage offers: provide sensible defaults even on non-daily
    # ticks so fallback decision objects validate consistently.
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
