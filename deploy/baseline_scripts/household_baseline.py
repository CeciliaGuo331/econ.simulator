"""Baseline household strategy used for Docker deployments.

This baseline only uses the household-visible whitelist fields. It is
defensive if `entity_state` is missing (in which case registry should
normally provide it for household scripts)."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp
import random


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

    # Randomized strategy: choose consumption budget and other required
    # fields randomly within sensible/legal bounds. Utility is computed by
    # the engine and depends only on realized consumption.
    liquid = cash + deposits
    max_affordable = max(1.0, liquid + wage_income)

    # consumption: random between minimum and a capped share of resources
    consumption_min = 1.0
    consumption_max = max(1.0, 0.5 * max_affordable)
    consumption = round(random.uniform(consumption_min, consumption_max), 2)

    # savings_rate: between 0 and 0.8
    savings_rate = round(random.uniform(0.0, 0.8), 3)

    features = context.get("world_state", {}).get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))

    # education: only applicable on daily ticks; choose randomly to study
    is_studying = False
    education_payment = 0.0
    if is_daily:
        # 20% chance to study
        if random.random() < 0.2:
            is_studying = True
            # pay up to a small fraction of cash, but at least 0.0
            education_payment = round(min(2.0, max(0.0, cash * 0.1)), 2)

    # labor supply: if studying -> 0.0; otherwise random 0 or 1 (work/no-work)
    if is_studying:
        labor_supply = 0.0
    else:
        labor_supply = 1.0 if random.random() < 0.7 else 0.0

    builder = OverridesBuilder()
    builder.household(
        entity_id,
        consumption_budget=consumption,
        savings_rate=savings_rate,
        labor_supply=labor_supply,
        **(
            {"is_studying": True, "education_payment": education_payment}
            if is_studying
            else {}
        ),
    )
    return builder.build()
