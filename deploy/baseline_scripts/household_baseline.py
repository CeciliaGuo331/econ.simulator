"""Baseline household strategy used for Docker deployments."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


Context = dict[str, object]
DecisionOverrides = dict[str, object]


def generate_decisions(context: Context) -> DecisionOverrides:
    entity_id_raw = context.get("entity_id")
    if entity_id_raw is None:
        return {}

    try:
        entity_id = int(entity_id_raw)
    except (TypeError, ValueError):
        return {}

    entity_state = context.get("entity_state") or {}
    if not entity_state:
        world_households = context.get("world_state", {}).get("households", {})
        entity_state = world_households.get(str(entity_id)) or world_households.get(
            entity_id
        )
        if entity_state is None:
            return {}

    world = context.get("world_state", {})
    macro = world.get("macro", {})

    inflation_factor = clamp(1.0 - macro.get("inflation", 0.0) * 0.5, 0.7, 1.1)
    unemployment = macro.get("unemployment_rate", 0.0)
    precaution = clamp(0.12 + unemployment * 0.25, 0.1, 0.45)

    balance_sheet = entity_state.get("balance_sheet", {})
    wage_income = entity_state.get("wage_income", 0.0)
    cash = balance_sheet.get("cash", 0.0)
    subsistence = 40.0
    discretionary = max(0.0, wage_income * (1 - precaution) + cash * 0.02)
    consumption_budget = max(subsistence, discretionary) * inflation_factor
    employment_status = str(entity_state.get("employment_status", "")).lower()
    labor_supply = 1.0 if employment_status.startswith("unemployed") else 0.85

    builder = OverridesBuilder()
    builder.household(
        entity_id,
        consumption_budget=round(consumption_budget, 2),
        savings_rate=round(precaution, 3),
        labor_supply=labor_supply,
    )

    return builder.build()
