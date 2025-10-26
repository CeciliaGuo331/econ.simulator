"""教育投资处理模块。

负责执行家户在决策中提交的教育支付：从家户转账到政府（或记为支出），
并将教育水平提升（education_level）写入 StateUpdateCommand。
"""

from __future__ import annotations

from typing import List, Tuple, Dict, Any

from ..data_access.models import (
    WorldState,
    TickDecisions,
    StateUpdateCommand,
    TickLogEntry,
    AgentKind,
)
from . import finance_market
from ..utils.settings import get_world_config


def process_education(
    world_state: WorldState, decisions: TickDecisions, tick: int, day: int
):
    updates: List[StateUpdateCommand] = []
    ledgers: List[Any] = []
    total_paid = 0.0
    students = []

    government = getattr(world_state, "government", None)
    if government is None:
        # no government to receive funds; skip processing
        return (
            updates,
            ledgers,
            TickLogEntry(
                tick=tick,
                day=day,
                message="education_skipped_no_government",
                context={},
            ),
        )

    try:
        cfg = get_world_config()
        gain = float(cfg.policies.education_gain)
    except Exception:
        gain = 0.05

    for hid, h_dec in decisions.households.items():
        try:
            if (
                getattr(h_dec, "is_studying", False)
                and float(getattr(h_dec, "education_payment", 0.0)) > 0
            ):
                amount = float(h_dec.education_payment)
                # transfer from household to government immediately (tuition paid now)
                t_updates, t_ledgers, t_log = finance_market.transfer(
                    world_state,
                    payer_kind=AgentKind.HOUSEHOLD,
                    payer_id=str(hid),
                    payee_kind=AgentKind.GOVERNMENT,
                    payee_id=government.id,
                    amount=amount,
                    tick=tick,
                    day=day,
                )
                # collect transfer updates/ledgers
                if t_updates:
                    updates.extend(t_updates)
                if t_ledgers:
                    ledgers.extend(t_ledgers)

                # mark household as studying for the current day; do NOT
                # immediately apply education level gains here — gains are
                # applied at the start of the next day's first tick by
                # daily_settlement.settle_previous_day.
                hh = world_state.households[int(hid)]
                hh.is_studying = True

                updates.append(
                    StateUpdateCommand.assign(
                        scope=AgentKind.HOUSEHOLD,
                        agent_id=hid,
                        is_studying=True,
                    )
                )

                total_paid += amount
                students.append(hid)
        except Exception:
            continue

    context = {"students": str(students), "total_paid": float(total_paid)}
    log = TickLogEntry(
        tick=tick, day=day, message="education_processed", context=context
    )
    return updates, ledgers, log
