"""示例外置政府脚本。"""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: dict) -> dict:
    world = context.get("world_state", {})
    macro = world.get("macro", {})
    gov = context.get("entity_state") or world.get("government") or {}

    unemployment = macro.get("unemployment_rate", 0.0)
    households = max(1, len(world.get("households", {})))

    tax_rate = clamp(
        gov.get("tax_rate", 0.15) - max(0.0, (0.06 - unemployment)) * 0.05, 0.05, 0.45
    )
    transfer_budget = round(
        households
        * gov.get("unemployment_benefit", 50.0)
        * max(0.0, unemployment - 0.05),
        2,
    )

    builder = OverridesBuilder()
    builder.government(
        tax_rate=round(tax_rate, 4),
        government_jobs=gov.get("employees", []).__len__(),
        transfer_budget=transfer_budget,
    )
    return builder.build()
