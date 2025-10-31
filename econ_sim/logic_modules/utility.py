"""Utility accumulation module.

This module computes per-household instantaneous utility from the
`household.last_consumption` value (written by `goods_market`) using a
CRRA utility function (configurable gamma) and accumulates the discounted
value into `household.lifetime_utility` (discounted back to tick 1 using
an exogenous per-tick discount factor `beta`).

Design choices / assumptions:
- world_state.tick is assumed to start at 1 for discount exponent computation.
- consumption used is `household.last_consumption` (if missing, treated as 0).
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
import math


def _compute_instant_utility(c: float, gamma: float, eps: float) -> float:
    # Shift consumption by +1 to make utility(0) == 0 and ensure positive
    # consumption yields positive utility. This avoids huge negative values
    # when c == 0 and gamma == 1 (log(0)). Using u(c) = log(1 + c) for
    # gamma == 1 and its CRRA generalization for other gamma preserves
    # homothetic preferences while making zero the reference.
    try:
        c_val = float(c) if c is not None else 0.0
    except Exception:
        c_val = 0.0
    c_shift = max(0.0, c_val) + 1.0

    if abs(float(gamma) - 1.0) < 1e-12:
        # shifted log utility: u(c) = log(1 + c)
        return math.log(c_shift)
    else:
        # shifted CRRA: u(c) = ((1+c)^(1-gamma) - 1) / (1-gamma)
        return (c_shift ** (1.0 - float(gamma)) - 1.0) / (1.0 - float(gamma))


def accumulate_utility(
    world_state: WorldState, *, tick: int, day: int
) -> Tuple[List[StateUpdateCommand], TickLogEntry]:
    cfg = get_world_config()
    beta = float(getattr(cfg.policies, "discount_factor_per_tick", 1.0))
    gamma = float(getattr(cfg.policies, "crra_gamma", 1.0))
    eps = float(getattr(cfg.policies, "utility_epsilon_for_log", 1e-8))

    updates: List[StateUpdateCommand] = []
    count_updated = 0

    # discount to tick 1: exponent = tick - 1 (assume ticks start at 1)
    try:
        exp = max(0, int(tick) - 1)
    except Exception:
        exp = 0
    try:
        discount = float(beta) ** exp if beta is not None else 1.0
    except Exception:
        discount = 1.0

    for hid, hh in world_state.households.items():
        try:
            c = getattr(hh, "last_consumption", 0.0) or 0.0
            u = _compute_instant_utility(c, gamma, eps)
            discounted = discount * float(u)
            # compute new cumulative utility using in-memory value
            prev = float(getattr(hh, "lifetime_utility", 0.0) or 0.0)
            new_total = prev + discounted
            # produce a set/assign so persistence writes the new value
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.HOUSEHOLD,
                    agent_id=hid,
                    lifetime_utility=new_total,
                    last_instant_utility=float(u),
                )
            )
            count_updated += 1
        except Exception:
            # best-effort: skip households that error
            continue

    log = TickLogEntry(
        tick=tick,
        day=day,
        message="utility_accumulated",
        context={"households_updated": count_updated},
    )

    return updates, log


__all__ = ["accumulate_utility"]
