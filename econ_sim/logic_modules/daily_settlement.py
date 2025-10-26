"""Daily settlement: on the first tick of each day, settle previous day's wages
and clear employment relationships.

This module is intended to be invoked by the orchestrator at the start of a
tick when `is_daily_decision_tick` is True. It will:
- Pay wages to firm.employees and government.employees using finance_market.transfer
- Clear employment_status and employer_id on households
- Clear firm's and government's employee lists and labor_assignment

The function mutates the in-memory `world_state` (so subsequent decision
generation observes the post-settlement state) and returns StateUpdateCommand
and TickLogEntry for persistence/audit.
"""

from __future__ import annotations

from typing import List, Tuple

from ..data_access.models import (
    WorldState,
    StateUpdateCommand,
    TickLogEntry,
    AgentKind,
)
from ..utils.settings import get_world_config


def settle_previous_day(
    world_state: WorldState, *, tick: int, day: int
) -> Tuple[List[StateUpdateCommand], TickLogEntry]:
    """Settle wages for previous day's employment and clear employment links.

    This mutates world_state in-place (household employment_status, firm.employees,
    government.employees) and returns updates to persist the changes.
    """
    updates: List[StateUpdateCommand] = []

    # best-effort: if components missing, skip gracefully
    firm = getattr(world_state, "firm", None)
    government = getattr(world_state, "government", None)

    from . import finance_market

    total_paid = 0.0
    paid_count = 0

    # pay firm employees
    if firm is not None and getattr(firm, "employees", None):
        wage = float(getattr(firm, "wage_offer", 0.0))
        # copy list to avoid mutation during iteration
        assigned = list(getattr(firm, "employees", []) or [])
        for hid in assigned:
            try:
                # use finance_market.transfer to perform atomic ledger+updates
                t_updates, t_ledgers, t_log = finance_market.transfer(
                    world_state,
                    payer_kind=AgentKind.FIRM,
                    payer_id=firm.id,
                    payee_kind=AgentKind.HOUSEHOLD,
                    payee_id=str(hid),
                    amount=wage,
                    tick=tick,
                    day=day,
                )
                # collect returned updates/ledgers into persisted updates
                if t_updates:
                    updates.extend(t_updates)
                total_paid += wage
                paid_count += 1
            except Exception:
                # ignore individual transfer failures (best-effort)
                continue

        # clear employment links in-memory
        for hid in assigned:
            hh = world_state.households.get(int(hid))
            if hh is not None:
                hh.employment_status = (
                    hh.employment_status.__class__.UNEMPLOYED
                    if hasattr(hh.employment_status, "__class__")
                    else "unemployed"
                )
                hh.employer_id = None
                hh.wage_income = 0.0

        # clear firm's employee list and labor_assignment
        firm.employees = []
        # also clear labor_assignment if present
        if hasattr(firm, "labor_assignment"):
            try:
                setattr(firm, "labor_assignment", [])
            except Exception:
                pass

        # persist firm employee clear
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.FIRM,
                agent_id=firm.id,
                employees=[],
                labor_assignment=[],
            )
        )

    # pay government employees similarly
    if government is not None and getattr(government, "employees", None):
        gov_wage = float(getattr(government, "unemployment_benefit", 50.0))
        assigned_gov = list(getattr(government, "employees", []) or [])
        for hid in assigned_gov:
            try:
                t_updates, t_ledgers, t_log = finance_market.transfer(
                    world_state,
                    payer_kind=AgentKind.GOVERNMENT,
                    payer_id=government.id,
                    payee_kind=AgentKind.HOUSEHOLD,
                    payee_id=str(hid),
                    amount=gov_wage,
                    tick=tick,
                    day=day,
                )
                if t_updates:
                    updates.extend(t_updates)
                total_paid += gov_wage
                paid_count += 1
            except Exception:
                continue

        for hid in assigned_gov:
            hh = world_state.households.get(int(hid))
            if hh is not None:
                hh.employment_status = (
                    hh.employment_status.__class__.UNEMPLOYED
                    if hasattr(hh.employment_status, "__class__")
                    else "unemployed"
                )
                hh.employer_id = None
                hh.wage_income = 0.0

        government.employees = []
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=government.id,
                employees=[],
            )
        )

    # 2) finalize education from previous day: for households who were
    # marked is_studying=True, apply the configured education_gain and clear
    # the studying flag so they can participate in labor markets again.
    try:
        cfg = get_world_config()
        gain = float(cfg.policies.education_gain)
    except Exception:
        gain = 0.05

    students_processed = 0
    for hid, hh in list(world_state.households.items()):
        try:
            if getattr(hh, "is_studying", False):
                # apply gain
                try:
                    current_level = float(hh.education_level or 0.0)
                except Exception:
                    current_level = 0.0
                new_level = min(1.5, current_level + gain)
                hh.education_level = new_level
                hh.is_studying = False
                updates.append(
                    StateUpdateCommand.assign(
                        AgentKind.HOUSEHOLD,
                        agent_id=hid,
                        education_level=new_level,
                        is_studying=False,
                    )
                )
                students_processed += 1
        except Exception:
            continue

    ctx = {"paid_count": paid_count, "total_paid": float(total_paid)}
    if students_processed:
        ctx["students_finalized"] = students_processed

    log = TickLogEntry(
        tick=tick,
        day=day,
        message="daily_settlement_executed",
        context=ctx,
    )

    return updates, log
