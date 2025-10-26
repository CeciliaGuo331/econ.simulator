"""新的劳动力市场子系统实现（最小版）。

撮合规则（实现自 docs/econ_design/market.md 与 agent.md）：
- 仅在决策中提交 labor_supply > 0 的家户参与匹配；
- 过滤掉 reservation_wage > wage_offer * 1.1 的候选者；
- 计算 human_capital_score = clip(0.4 + 0.6 * (productivity / max(mean_prod,0.1)), 0.1, 2.0)
- 生成 epsilon ~ Uniform(0,1)（使用可复现 RNG），matching_score = 0.8 * human_capital_score + 0.2 * epsilon
- 按 matching_score 降序选前 slots 名家户分配岗位。

返回值： (updates, log_entry)
其中 updates 为 StateUpdateCommand 列表，log_entry 为 TickLogEntry，context 中包含分配到的家户 id 列表。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from ..data_access.models import (
    WorldState,
    TickDecisions,
    StateUpdateCommand,
    TickLogEntry,
    AgentKind,
    EmploymentStatus,
)
from ..utils.settings import get_world_config


def resolve_labor_market_new(
    world_state: WorldState, decisions: TickDecisions
) -> Tuple[List[StateUpdateCommand], TickLogEntry]:
    """Match workers to firm and government vacancies.

    This minimal implementation supports a single firm and government as in
    the existing world_state model, but is written to be clear and testable.
    """
    cfg = get_world_config()

    firm = world_state.firm
    government = world_state.government
    if firm is None or government is None:
        raise ValueError("Missing firm or government in world_state for labor market")

    # Build candidate pool: households that signaled labor_supply > 0
    candidates = []  # list of (hid, productivity, reservation_wage)
    for hid, h_dec in decisions.households.items():
        if getattr(h_dec, "labor_supply", 0.0) <= 0.0:
            continue
        h = world_state.households[hid]
        # skip households who are studying for the current day
        if getattr(h, "is_studying", False):
            continue
        # HouseholdState exposes `skill` (not `productivity`) in the data model
        candidates.append((hid, float(h.skill), float(h.reservation_wage)))

    # desired slots: use firm's hiring_demand from decisions.firm
    desired_firm_slots = max(0, int(decisions.firm.hiring_demand))
    desired_gov_slots = max(0, int(decisions.government.government_jobs))

    # Prepare RNG deterministic by world tick and config seed
    seed = int(cfg.simulation.seed or 0) + int(world_state.tick)
    rng = np.random.default_rng(seed)

    # helper to compute matching scores given a wage_offer
    def score_candidates(cands, wage_offer):
        prods = [p for (_, p, _) in cands]
        mean_prod = float(np.mean(prods)) if prods else 0.1
        scored = []
        for hid, prod, res_w in cands:
            # filter by reservation wage relative to wage_offer
            if res_w > wage_offer * 1.1:
                continue
            human_capital = max(0.1, min(2.0, 0.4 + 0.6 * (prod / max(mean_prod, 0.1))))
            epsilon = float(rng.uniform(0.0, 1.0))
            matching_score = 0.8 * human_capital + 0.2 * epsilon
            scored.append((hid, matching_score))
        # sort by score desc
        scored.sort(key=lambda x: x[1], reverse=True)
        return [hid for hid, _ in scored]

    assigned_firm: List[int] = []
    assigned_gov: List[int] = []

    # firm matching
    firm_candidates = [(hid, prod, res) for (hid, prod, res) in candidates]
    firm_sorted = score_candidates(firm_candidates, decisions.firm.wage_offer)
    assigned_firm = firm_sorted[:desired_firm_slots]

    # remove assigned from pool for government
    remaining = [c for c in candidates if c[0] not in assigned_firm]

    gov_sorted = score_candidates(remaining, decisions.firm.wage_offer * 0.8)
    assigned_gov = gov_sorted[:desired_gov_slots]

    updates: List[StateUpdateCommand] = []

    # Update households assigned to firm
    for hid in assigned_firm:
        h = world_state.households[hid]
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD,
                agent_id=hid,
                employment_status=EmploymentStatus.EMPLOYED_FIRM.value,
                employer_id=firm.id,
                wage_income=decisions.firm.wage_offer,
            )
        )

    # Update households assigned to government
    for hid in assigned_gov:
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD,
                agent_id=hid,
                employment_status=EmploymentStatus.EMPLOYED_GOVERNMENT.value,
                employer_id=government.id,
                wage_income=decisions.firm.wage_offer * 0.8,
            )
        )

    # Update firm employee list
    new_firm_employees = sorted(list(set(firm.employees + assigned_firm)))
    # Also record explicit labor_assignment for auditing and for production module
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.FIRM,
            agent_id=firm.id,
            employees=new_firm_employees,
            labor_assignment=assigned_firm,
        )
    )

    # Update government employee list
    new_gov_employees = sorted(
        list(set((world_state.government.employees or []) + assigned_gov))
    )
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.GOVERNMENT,
            agent_id=government.id,
            employees=new_gov_employees,
        )
    )

    # Log
    context = {
        "firm_headcount": len(new_firm_employees),
        "government_headcount": len(new_gov_employees),
        "assigned_firm": assigned_firm,
        "assigned_government": assigned_gov,
    }
    # serialize lists to strings for TickLogEntry compatibility
    import json

    context_serial = {
        k: json.dumps(v) if isinstance(v, list) else v for k, v in context.items()
    }

    log = TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="labor_market_cleared_new",
        context=context_serial,
    )

    return updates, log
