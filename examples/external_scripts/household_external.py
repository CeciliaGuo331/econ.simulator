"""示例家户脚本（仅使用允许读取的字段）。"""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp
import random


def generate_decisions(context: dict) -> dict:
    entity_id = context.get("entity_id")
    if entity_id is None:
        return {}

    ent = context.get("entity_state") or {}
    if not ent:
        return {}

    bs = ent.get("balance_sheet", {})
    cash = float(bs.get("cash", 0.0))
    deposits = float(bs.get("deposits", 0.0))
    wage = float(ent.get("wage_income", 0.0))

    # randomized decisions: consumption, savings_rate, labor_supply, education
    liquid = cash + deposits
    max_affordable = max(1.0, liquid + wage)
    consumption_min = 1.0
    consumption_max = max(1.0, 0.5 * max_affordable)
    consumption = round(random.uniform(consumption_min, consumption_max), 2)

    savings_rate = round(random.uniform(0.0, 0.8), 3)

    features = context.get("world_state", {}).get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))

    is_studying = False
    education_payment = 0.0
    if is_daily and random.random() < 0.2:
        is_studying = True
        education_payment = round(min(2.0, max(0.0, cash * 0.1)), 2)

    if is_studying:
        labor_supply = 0.0
    else:
        labor_supply = 1.0 if random.random() < 0.7 else 0.0

    builder = OverridesBuilder()
    builder.household(
        int(entity_id),
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
