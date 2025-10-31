"""Baseline household strategy used for Docker deployments.

This baseline only uses the household-visible whitelist fields. It is
defensive if `entity_state` is missing (in which case registry should
normally provide it for household scripts)."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: dict) -> dict:
    entity_id_raw = context.get("entity_id")
    if entity_id_raw is None:
        return {}

    try:
        entity_id = int(entity_id_raw)
    except (TypeError, ValueError):
        return {}

    # entity_state for household scripts is expected to be the pruned own-record
    ent = context.get("entity_state") or {}
    if not ent:
        # defensive: do not attempt to read other households; fallback empty
        return {}

    bs = ent.get("balance_sheet", {})
    cash = float(bs.get("cash", 0.0))
    deposits = float(bs.get("deposits", 0.0))
    wage_income = float(ent.get("wage_income", 0.0))

    # simple consumption rule using only allowed fields
    liquid = cash + deposits
    base = max(1.0, 0.05 * liquid + 0.5 * wage_income)

    features = context.get("world_state", {}).get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))

    builder = OverridesBuilder()

    # education decision only on daily ticks and using allowed fields
    is_studying = False
    edu_payment = 0.0
    if is_daily:
        edu_level = float(ent.get("education_level", 0.0))
        if edu_level < 0.4:
            is_studying = True
            edu_payment = 2.0

    labor_supply = 1.0
    if is_daily:
        emp = str(ent.get("employment_status", "")).lower()
        labor_supply = 1.0 if emp.startswith("unemployed") else 0.85
    if is_studying:
        labor_supply = 0.0

    household_fields = {
        "consumption_budget": round(base, 2),
        "savings_rate": 0.1,
        "labor_supply": labor_supply,
    }
    if is_studying:
        household_fields["is_studying"] = True
        household_fields["education_payment"] = edu_payment

    builder.household(entity_id, **household_fields)
    return builder.build()
