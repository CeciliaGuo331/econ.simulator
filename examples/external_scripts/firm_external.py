"""示例外置企业脚本 — 简单的生产/定价/招聘策略。"""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: dict) -> dict:
    world = context.get("world_state", {})
    features = world.get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))
    firm = context.get("entity_state") or world.get("firm") or {}

    households = world.get("households", {})
    demand = max(1.0, sum(h.get("last_consumption", 0.0) for h in households.values()))

    planned = round(demand * 0.5, 2)
    price = round(firm.get("price", 10.0) * 1.0, 2)

    builder = OverridesBuilder()
    if is_daily:
        # only propose hiring on daily ticks
        required_workers = int(planned / max(0.1, firm.get("productivity", 1.0)))
        hiring = max(0, required_workers - len(firm.get("employees", [])))
        builder.firm(
            planned_production=planned,
            price=price,
            hiring_demand=hiring,
            wage_offer=round(firm.get("wage_offer", 80.0), 2),
        )
    else:
        builder.firm(planned_production=planned, price=price)

    return builder.build()
