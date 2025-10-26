"""Firm production execution module.

This module computes realized output based on the firm's capital stock,
assigned labor (employee list) and technology using a Cobb-Douglas
production function. It writes inventory increases and a small spoilage
adjustment back to the firm's balance sheet via StateUpdateCommand.

The intent: strategy scripts decide targets (planned_production), hiring and
pricing; the production engine computes the actual output from realized
inputs so scripts cannot directly set `output`.
"""

from __future__ import annotations

from typing import List, Tuple, Any
from ..data_access.models import (
    WorldState,
    TickDecisions,
    StateUpdateCommand,
    TickLogEntry,
    AgentKind,
)


def process_production(
    world_state: WorldState, decisions: TickDecisions, *, tick: int, day: int
) -> Tuple[List[StateUpdateCommand], TickLogEntry]:
    """Compute realized production and return state updates + log.

    - Computes effective labour from firm.employees using household state
      (education_level and skill) and optional household_shocks.
    - Applies Cobb-Douglas: output = technology * K^alpha * L^(1-alpha)
    - Updates firm's inventory_goods by adding output and subtracting spoilage.
    """
    updates: List[StateUpdateCommand] = []

    firm = world_state.firm
    if firm is None:
        return updates, TickLogEntry(
            tick=tick, day=day, message="production_skipped_no_firm"
        )

    # parameters
    alpha = 0.33
    technology = float(getattr(firm, "technology", 1.0))
    capital_stock = float(getattr(firm, "capital_stock", 150.0))

    # compute effective labour: sum over employees of household productivity
    effective_labor = 0.0
    for hid in getattr(firm, "employees", []) or []:
        hh = world_state.households.get(int(hid))
        if hh is None:
            continue
        # household productivity formula per docs: ability * (1 + 0.6 * education_level) * (1 + shock)
        ability = float(getattr(hh, "skill", 1.0))
        edu = float(getattr(hh, "education_level", 0.5))
        shock = 0.0
        hs = (
            world_state.household_shocks.get(int(hid))
            if getattr(world_state, "household_shocks", None)
            else None
        )
        if hs is not None:
            shock = float(getattr(hs, "ability_multiplier", 1.0) - 1.0)
        prod = ability * (1.0 + 0.6 * edu) * (1.0 + shock)
        effective_labor += prod

    # avoid zero labour
    effective_labor = max(0.0, effective_labor)

    # Cobb-Douglas production
    try:
        output = float(
            technology * (capital_stock**alpha) * (effective_labor ** (1.0 - alpha))
        )
    except Exception:
        output = 0.0

    # spoilage (small fraction of inventory)
    current_inventory = float(firm.balance_sheet.inventory_goods or 0.0)
    spoilage = 0.01 * current_inventory

    new_inventory = max(0.0, current_inventory + output - spoilage)

    # prepare balance sheet update (only override inventory_goods)
    bs = firm.balance_sheet.model_dump()
    bs["inventory_goods"] = new_inventory

    updates.append(
        StateUpdateCommand.assign(
            AgentKind.FIRM,
            agent_id=firm.id,
            balance_sheet=bs,
            last_production=round(output, 4),
        )
    )

    log = TickLogEntry(
        tick=tick,
        day=day,
        message="production_executed",
        context={
            "produced": float(output),
            "spoilage": float(spoilage),
            "effective_labor": float(effective_labor),
        },
    )

    return updates, log
