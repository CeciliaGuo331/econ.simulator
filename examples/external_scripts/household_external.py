"""示例家户脚本（仅使用允许读取的字段）。"""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


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

    # 简单 PIH 风格消费（只使用允许字段）
    cfg = context.get("config", {}) or {}
    beta = float(cfg.get("policies", {}).get("discount_factor_per_tick", 0.999))
    liquid = cash + deposits
    consumption = round(max(1.0, (1.0 - beta) * (liquid + wage)), 2)
    savings_rate = round(clamp(0.15, 0.01, 0.6), 3)

    features = context.get("world_state", {}).get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))

    builder = OverridesBuilder()
    if is_daily:
        pay = round(min(2.0, wage * 0.05 + cash * 0.01), 2)
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
