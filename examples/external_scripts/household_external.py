"""示例外置家户脚本 — 展示用户如何实现自定义决策。

此脚本可以被上传并挂载到仿真实例的某个 household。
"""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: dict) -> dict:
    entity_id = context.get("entity_id")
    if entity_id is None:
        return {}

    world = context.get("world_state", {})
    features = world.get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))

    entity_state = context.get("entity_state") or {}
    balance = entity_state.get("balance_sheet", {})
    cash = balance.get("cash", 0.0)
    wage = entity_state.get("wage_income", 0.0)

    # lifetime-utility-aware approximate consumption: PIH-style fraction of wealth
    cfg = context.get("config", {}) or {}
    policies = cfg.get("policies", {})
    beta = float(policies.get("discount_factor_per_tick", 0.999))
    deposits = float(balance.get("deposits", 0.0))
    expected_income = float(wage)
    liquid_wealth = float(cash) + deposits
    consumption = round(max(1.0, (1.0 - beta) * (liquid_wealth + expected_income)), 2)
    savings_rate = round(clamp(0.15, 0.01, 0.6), 3)

    builder = OverridesBuilder()
    # only set labor/education on daily ticks
    if is_daily:
        edu_cost = float(
            context.get("config", {})
            .get("policies", {})
            .get("education_cost_per_day", 2.0)
        )
        pay = round(min(edu_cost, wage * 0.05 + cash * 0.01), 2)
        builder.household(
            int(entity_id),
            consumption_budget=consumption,
            savings_rate=savings_rate,
            labor_supply=1.0,
            is_studying=pay > 0,
            education_payment=pay,
        )
    else:
        builder.household(
            int(entity_id), consumption_budget=consumption, savings_rate=savings_rate
        )

    return builder.build()
