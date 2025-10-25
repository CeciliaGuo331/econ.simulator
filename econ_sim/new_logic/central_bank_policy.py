"""央行公开市场操作（OMO）实现（最小化）。

功能：
- open_market_operation(world_state, bond_id, quantity, side, price, tick, day)
  side: "buy" (央行买入，向银行提供流动性) 或 "sell" (央行卖出，回收流动性)

注意：该实现与 ledger/state 更新保持一致性，但为最小实现，央行持仓仅记录在 central_bank.bond_holdings。
"""

from __future__ import annotations

from typing import Dict, Any

from econ_sim.data_access.models import AgentKind, LedgerEntry, StateUpdateCommand


def open_market_operation(
    world_state,
    bond_id: str,
    quantity: float,
    side: str,
    price: float,
    tick: int,
    day: int,
):
    central = world_state.central_bank
    bank = world_state.bank
    ledgers = []
    updates = []

    amount = quantity * price

    if side == "buy":
        # central bank buys bonds from bank -> bank receives cash, central receives bonds
        available = bank.bond_holdings.get(bond_id, 0.0)
        qty = min(available, quantity)
        if qty <= 0:
            return {"updates": [], "ledgers": [], "transacted_quantity": 0}

        bank.balance_sheet.cash += qty * price
        central.balance_sheet.cash -= qty * price
        bank.bond_holdings[bond_id] = available - qty
        central.bond_holdings[bond_id] = central.bond_holdings.get(bond_id, 0.0) + qty

        ledgers.append(
            LedgerEntry(
                tick=tick,
                day=day,
                account_kind=AgentKind.BANK,
                entity_id=bank.id,
                entry_type="bond_sale_to_cb",
                amount=qty * price,
                balance_after=bank.balance_sheet.cash,
                reference=bond_id,
            )
        )
        ledgers.append(
            LedgerEntry(
                tick=tick,
                day=day,
                account_kind=AgentKind.CENTRAL_BANK,
                entity_id=central.id,
                entry_type="bond_purchase",
                amount=-qty * price,
                balance_after=central.balance_sheet.cash,
                reference=bond_id,
            )
        )

        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.BANK,
                agent_id=bank.id,
                balance_sheet=bank.balance_sheet.model_dump(),
                bond_holdings=bank.bond_holdings,
            )
        )
        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.CENTRAL_BANK,
                agent_id=central.id,
                balance_sheet=central.balance_sheet.model_dump(),
                bond_holdings=central.bond_holdings,
            )
        )

        return {"updates": updates, "ledgers": ledgers, "transacted_quantity": qty}

    elif side == "sell":
        # central bank sells bonds to bank -> bank pays cash, central receives cash
        central_hold = central.bond_holdings.get(bond_id, 0.0)
        qty = min(central_hold, quantity)
        if qty <= 0:
            return {"updates": [], "ledgers": [], "transacted_quantity": 0}

        bank.balance_sheet.cash -= qty * price
        central.balance_sheet.cash += qty * price
        central.bond_holdings[bond_id] = central_hold - qty
        bank.bond_holdings[bond_id] = bank.bond_holdings.get(bond_id, 0.0) + qty

        ledgers.append(
            LedgerEntry(
                tick=tick,
                day=day,
                account_kind=AgentKind.BANK,
                entity_id=bank.id,
                entry_type="bond_purchase_from_cb",
                amount=-qty * price,
                balance_after=bank.balance_sheet.cash,
                reference=bond_id,
            )
        )
        ledgers.append(
            LedgerEntry(
                tick=tick,
                day=day,
                account_kind=AgentKind.CENTRAL_BANK,
                entity_id=central.id,
                entry_type="bond_sale",
                amount=qty * price,
                balance_after=central.balance_sheet.cash,
                reference=bond_id,
            )
        )

        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.BANK,
                agent_id=bank.id,
                balance_sheet=bank.balance_sheet.model_dump(),
                bond_holdings=bank.bond_holdings,
            )
        )
        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.CENTRAL_BANK,
                agent_id=central.id,
                balance_sheet=central.balance_sheet.model_dump(),
                bond_holdings=central.bond_holdings,
            )
        )

        return {"updates": updates, "ledgers": ledgers, "transacted_quantity": qty}

    else:
        raise ValueError("side must be 'buy' or 'sell'")
