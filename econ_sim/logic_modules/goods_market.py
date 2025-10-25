"""新的商品市场子系统实现（最小版）。

函数 clear_goods_market_new 接受 WorldState 与 TickDecisions，按简单集合竞价 + 配给
逻辑将家庭的消费计划与企业库存匹配，返回用于持久化的 StateUpdateCommand 列表与日志。

该实现尽量保持简单、可测试，后续会扩展为更复杂的优先级/限价匹配逻辑。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..data_access.models import (
    WorldState,
    TickDecisions,
    StateUpdateCommand,
    TickLogEntry,
    AgentKind,
)


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
    for hid, h_dec in decisions.households.items():
        bid_price = getattr(h_dec, "bid_price", None)
        if bid_price is None:
            bid_price = ask_price
        # avoid division by zero
        qty = 0.0
        try:
            qty = float(max(0.0, h_dec.consumption_budget / bid_price))
        except Exception:
            qty = 0.0
        buy_orders.append((hid, qty, float(bid_price)))

    # Sort buyers by bid_price desc (price priority). Ties broken by agent id asc to be deterministic.
    buy_orders.sort(key=lambda x: (-x[2], x[0]))

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

        # Update household: deduct cash and set last_consumption
        new_cash = max(0.0, world_state.households[hid].balance_sheet.cash - payment)
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
    new_cash = float(world_state.firm.balance_sheet.cash + consumption_value)
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.FIRM,
            agent_id=world_state.firm.id,
            balance_sheet={
                **world_state.firm.balance_sheet.model_dump(),
                "inventory_goods": new_inventory,
                "cash": new_cash,
            },
            last_sales=goods_sold,
        )
    )

    # Macro update (simple)
    updates.append(
        StateUpdateCommand.assign(
            AgentKind.MACRO,
            agent_id=None,
            gdp=consumption_value,
        )
    )

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
            "trade_success": json.dumps(trade_success_serial),
            "trade_qty": json.dumps(trade_qty_serial),
        },
    )

    return updates, log
