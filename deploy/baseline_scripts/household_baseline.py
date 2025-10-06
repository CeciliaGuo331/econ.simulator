"""Baseline household strategy used for Docker deployments."""

from __future__ import annotations

from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    world = context["world_state"]
    macro = world["macro"]

    builder = OverridesBuilder()
    inflation_factor = clamp(1.0 - macro.get("inflation", 0.0) * 0.5, 0.7, 1.1)
    unemployment = macro.get("unemployment_rate", 0.0)
    precaution = clamp(0.12 + unemployment * 0.25, 0.1, 0.45)

    for raw_id, data in world["households"].items():
        hid = int(raw_id)
        balance_sheet = data.get("balance_sheet", {})
        wage_income = data.get("wage_income", 0.0)
        cash = balance_sheet.get("cash", 0.0)
        subsistence = 40.0
        discretionary = max(0.0, wage_income * (1 - precaution) + cash * 0.02)
        consumption_budget = max(subsistence, discretionary) * inflation_factor
        employment_status = str(data.get("employment_status", "")).lower()
        labor_supply = 1.0 if employment_status.startswith("unemployed") else 0.85

        builder.household(
            hid,
            consumption_budget=round(consumption_budget, 2),
            savings_rate=round(precaution, 3),
            labor_supply=labor_supply,
        )

    return builder.build()
