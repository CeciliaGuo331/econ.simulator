"""示例外置商业银行脚本。"""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: dict) -> dict:
    world = context.get("world_state", {})
    cb = world.get("central_bank", {})
    bank = context.get("entity_state") or world.get("bank") or {}

    policy_rate = cb.get("base_rate", 0.02)
    spread = clamp(0.02 + policy_rate * 0.5, 0.01, 0.06)
    loan_rate = clamp(policy_rate + spread, 0.02, 0.25)
    deposit_rate = clamp(policy_rate * 0.6, 0.0, loan_rate - 0.005)

    builder = OverridesBuilder()
    builder.bank(
        deposit_rate=round(deposit_rate, 4),
        loan_rate=round(loan_rate, 4),
        loan_supply=max(0.0, bank.get("balance_sheet", {}).get("deposits", 0.0) * 0.5),
    )
    return builder.build()
