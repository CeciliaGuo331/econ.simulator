"""Baseline central bank strategy for Docker deployments."""

from __future__ import annotations

from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]
    cb = world["central_bank"]

    builder = OverridesBuilder()

    inflation_gap = macro.get("inflation", 0.0) - cb.get("inflation_target", 0.02)
    unemployment_gap = macro.get("unemployment_rate", 0.0) - cb.get(
        "unemployment_target", 0.05
    )

    policy_rate = clamp(
        cb.get("base_rate", 0.02) + 0.8 * inflation_gap - 0.4 * unemployment_gap,
        0.0,
        0.25,
    )
    reserve_ratio = clamp(
        cb.get("reserve_ratio", 0.1) + 0.15 * unemployment_gap, 0.05, 0.35
    )

    builder.central_bank(
        policy_rate=round(policy_rate, 4),
        reserve_ratio=round(reserve_ratio, 4),
    )

    return builder.build()
