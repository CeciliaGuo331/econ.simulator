"""新的商品市场子系统实现（最小版）。

函数 clear_goods_market_new 接受 WorldState 与 TickDecisions，按简单集合竞价 + 配给
逻辑将家庭的消费计划与企业库存匹配，返回用于持久化的 StateUpdateCommand 列表与日志。

该实现尽量保持简单、可测试，后续会扩展为更复杂的优先级/限价匹配逻辑。
"""

from __future__ import annotations

from typing import Dict, List, Tuple
import random

from ..utils.settings import get_world_config

from ..data_access.models import (
    WorldState,
    TickDecisions,
    StateUpdateCommand,
    TickLogEntry,
    AgentKind,
)
from . import finance_market


def clear_goods_market_new(
    world_state: WorldState, decisions: TickDecisions
) -> Tuple[List[StateUpdateCommand], TickLogEntry]:
    """Minimal goods market clearing.

    Rules:
    - Each household has a `consumption_budget` in decisions.households
    - Firm offers `inventory_goods` from its balance sheet at `firm.price`
    - If total demand <= inventory => everyone gets full demand
    - Else allocate proportionally to planned quantities

    Returns a tuple (updates, log_entry).
    """
    firm = world_state.firm
    if firm is None:
        raise ValueError("No firm present in world_state")

    ask_price = max(0.01, float(firm.price))

    # Build buy orders: each household may optionally include a 'bid_price' field in decision
    # If not present, default bid_price = ask_price. Quantity = consumption_budget / bid_price
    buy_orders: List[tuple[int, float, float]] = []  # (hid, qty, bid_price)
    # read subsistence consumption from config and ensure households' budgets
    # are at least enough to purchase subsistence_consumption units at their bid_price.
    try:
        cfg = get_world_config()
        subsistence = float(cfg.markets.goods.subsistence_consumption)
    except Exception:
        subsistence = 1.0

    clipped_budgets: dict[int, tuple[float, float]] = {}
    for hid, h_dec in decisions.households.items():
        bid_price = getattr(h_dec, "bid_price", None)
        if bid_price is None:
            bid_price = ask_price
        bid_price = float(bid_price)

        # compute minimum budget required to secure subsistence consumption
        min_budget = subsistence * bid_price

        # original budget (may be None or invalid)
        try:
            orig_budget = float(getattr(h_dec, "consumption_budget", 0.0) or 0.0)
        except Exception:
            orig_budget = 0.0

        # clip up to at least min_budget to avoid zero consumption -> negative utility
        budget = max(orig_budget, min_budget)
        if budget != orig_budget:
            clipped_budgets[hid] = (orig_budget, budget)

        # avoid division by zero
        qty = 0.0
        try:
            qty = float(max(0.0, budget / bid_price))
        except Exception:
            qty = 0.0
        buy_orders.append((hid, qty, float(bid_price)))

    # Sort buyers by bid_price desc (price priority).
    # Ties should be broken by a reproducible RNG seeded with global seed + tick
    try:
        cfg = get_world_config()
        seed = int(cfg.simulation.seed or 0) + int(world_state.tick)
    except Exception:
        seed = int(world_state.tick)

    rnd = random.Random(seed)
    # attach a deterministic random tie-breaker to each order
    buy_orders_with_tie = [
        (hid, qty, bid_price, rnd.random()) for (hid, qty, bid_price) in buy_orders
    ]
    # sort by price desc, then tie-breaker asc (random), then hid asc for final determinism
    buy_orders_with_tie.sort(key=lambda x: (-x[2], x[3], x[0]))
    # drop tie values
    buy_orders = [
        (hid, qty, bid_price) for (hid, qty, bid_price, _) in buy_orders_with_tie
    ]

    available = float(world_state.firm.balance_sheet.inventory_goods)

    updates: List[StateUpdateCommand] = []
    goods_sold = 0.0
    consumption_value = 0.0

    trade_success: Dict[int, bool] = {}
    trade_qty: Dict[int, float] = {}

    # Allocate inventory to buyers by price priority. Use seller's ask_price as trade price.
    for hid, qty, bid_price in buy_orders:
        if available <= 0.0 or qty <= 0.0:
            trade_success[hid] = False
            trade_qty[hid] = 0.0
            continue
        fill = min(qty, available)
        payment = fill * ask_price
        goods_sold += fill
        consumption_value += payment
        available -= fill

        # Use finance_market.transfer to produce atomic updates + ledger
        t_updates, t_ledgers, t_log = finance_market.transfer(
            world_state,
            payer_kind=AgentKind.HOUSEHOLD,
            payer_id=str(hid),
            payee_kind=AgentKind.FIRM,
            payee_id=world_state.firm.id,
            amount=payment,
            tick=world_state.tick,
            day=world_state.day,
        )
        # attach last_consumption and trade_success into household update if present
        if t_updates:
            # find household update and augment it by setting the top-level
            # `last_consumption` field (not nesting inside balance_sheet).
            for up in t_updates:
                if up.scope is AgentKind.HOUSEHOLD and str(up.agent_id) == str(hid):
                    # set top-level last_consumption so HouseholdState is updated
                    up.changes["last_consumption"] = fill
                    # Also update in-memory world_state so downstream modules
                    # (e.g. utility.accumulate_utility) that read the live
                    # world_state observe the delivered consumption.
                    try:
                        # safe in-place update; households keyed by int
                        if hid in world_state.households:
                            world_state.households[hid].last_consumption = float(fill)
                    except Exception:
                        # best-effort: do not fail market clearing if in-memory write fails
                        pass
            updates.extend(t_updates)
        else:
            # fallback to previous behavior if finance market didn't return updates
            new_cash = max(
                0.0, world_state.households[hid].balance_sheet.cash - payment
            )
            updates.append(
                StateUpdateCommand.assign(
                    AgentKind.HOUSEHOLD,
                    agent_id=hid,
                    balance_sheet={
                        **world_state.households[hid].balance_sheet.model_dump(),
                        "cash": new_cash,
                    },
                    last_consumption=fill,
                    trade_success=True,
                )
            )
        trade_success[hid] = True
        trade_qty[hid] = fill

    # Update firm with aggregated results
    new_inventory = max(
        0.0, world_state.firm.balance_sheet.inventory_goods - goods_sold
    )
    # The firm's cash will have been updated by finance_market.transfer when
    # transfers succeeded. Do not add consumption_value again to avoid
    # double-counting. Build the firm's balance_sheet from the current model
    # dump and only override the inventory field.
    firm_bs = world_state.firm.balance_sheet.model_dump()
    firm_bs["inventory_goods"] = new_inventory
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.FIRM,
            agent_id=world_state.firm.id,
            balance_sheet=firm_bs,
            last_sales=goods_sold,
        )
    )

    # NOTE: GDP is computed at financial settlement (finance_market.transfer)
    # as a best-effort single source of truth. The transfer call that
    # represents household -> firm payments will append a MACRO gdp update.

    # convert trade maps to serializable simple types (string keys)
    import json

    trade_success_serial = {str(k): (1 if v else 0) for k, v in trade_success.items()}
    trade_qty_serial = {str(k): float(v) for k, v in trade_qty.items()}

    # serialize nested maps as JSON strings to satisfy TickLogEntry.context typing
    log = TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="goods_market_cleared_new",
        context={
            "goods_sold": float(goods_sold),
            "consumption_value": float(consumption_value),
            "clipped_budgets": json.dumps(
                {str(k): [v[0], v[1]] for k, v in clipped_budgets.items()}
            ),
            "trade_success": json.dumps(trade_success_serial),
            "trade_qty": json.dumps(trade_qty_serial),
        },
    )

    return updates, log
