"""Baseline central bank strategy for Docker deployments."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


Context = dict[str, object]
DecisionOverrides = dict[str, object]


def generate_decisions(context: Context) -> DecisionOverrides:
    world = context.get("world_state", {})
    macro = world.get("macro", {})
    cb = context.get("entity_state") or world.get("central_bank")

    if not cb:
        return {}

    builder = OverridesBuilder()

    inflation_gap = macro.get("inflation", 0.0) - cb.get("inflation_target", 0.02)
    unemployment_gap = macro.get("unemployment_rate", 0.0) - cb.get(
        "unemployment_target", 0.05
    )

    # Taylor-style policy rate with smoothing toward previous rate
    taylor_rate = (
        cb.get("base_rate", 0.02) + 1.5 * inflation_gap - 0.5 * unemployment_gap
    )
    taylor_rate = clamp(taylor_rate, 0.0, 0.4)

    prev_rate = cb.get("base_rate", 0.02)
    smoothing = 0.7
    policy_rate = round(smoothing * prev_rate + (1 - smoothing) * taylor_rate, 4)

    # reserve ratio adjusts to credit growth: require credit_growth from macro if present
    credit_growth = macro.get("credit_growth", None)
    reserve_ratio = cb.get("reserve_ratio", 0.1)
    if credit_growth is not None:
        reserve_ratio = clamp(reserve_ratio + 0.1 * (credit_growth - 0.03), 0.05, 0.2)

    builder.central_bank(policy_rate=policy_rate, reserve_ratio=round(reserve_ratio, 4))

    return builder.build()
