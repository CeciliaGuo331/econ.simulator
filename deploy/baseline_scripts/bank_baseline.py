"""Baseline commercial bank strategy for Docker deployments."""

from __future__ import annotations

from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    bank = world["bank"]
    central_bank = world["central_bank"]

    builder = OverridesBuilder()

    policy_rate = central_bank.get("base_rate", 0.02)
    reserve_ratio = central_bank.get("reserve_ratio", 0.1)
    deposits = bank["balance_sheet"].get("deposits", 0.0)
    loans = bank["balance_sheet"].get("loans", 0.0)

    spread = clamp(0.025 + policy_rate * 0.5, 0.02, 0.05)
    loan_rate = clamp(policy_rate + spread, 0.02, 0.25)
    deposit_rate = clamp(policy_rate * 0.65, 0.0, loan_rate - 0.005)
    loan_supply = max(0.0, deposits * (1 - reserve_ratio) - loans)

    builder.bank(
        deposit_rate=round(deposit_rate, 4),
        loan_rate=round(loan_rate, 4),
        loan_supply=round(loan_supply, 2),
    )

    return builder.build()
