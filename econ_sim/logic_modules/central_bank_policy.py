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

        from . import finance_market

        try:
            t_updates, t_ledgers, t_log = finance_market.transfer(
                world_state,
                payer_kind=AgentKind.CENTRAL_BANK,
                payer_id=central.id,
                payee_kind=AgentKind.BANK,
                payee_id=bank.id,
                amount=qty * price,
                tick=tick,
                day=day,
            )
            updates.extend(t_updates)
            ledgers.extend(t_ledgers)
        except Exception:
            # Do not mutate balance sheets directly here; log and continue.
            import logging

            logging.getLogger(__name__).exception(
                "finance_market.transfer failed during central_bank_policy buy; cash transfer skipped"
            )

        bank.bond_holdings[bond_id] = available - qty
        central.bond_holdings[bond_id] = central.bond_holdings.get(bond_id, 0.0) + qty

        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.BANK,
                agent_id=bank.id,
                bond_holdings=bank.bond_holdings,
            )
        )
        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.CENTRAL_BANK,
                agent_id=central.id,
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

        from . import finance_market

        try:
            t_updates, t_ledgers, t_log = finance_market.transfer(
                world_state,
                payer_kind=AgentKind.BANK,
                payer_id=bank.id,
                payee_kind=AgentKind.CENTRAL_BANK,
                payee_id=central.id,
                amount=qty * price,
                tick=tick,
                day=day,
            )
            updates.extend(t_updates)
            ledgers.extend(t_ledgers)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "finance_market.transfer failed during central_bank_policy sell; cash transfer skipped"
            )

        central.bond_holdings[bond_id] = central_hold - qty
        bank.bond_holdings[bond_id] = bank.bond_holdings.get(bond_id, 0.0) + qty

        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.BANK,
                agent_id=bank.id,
                bond_holdings=bank.bond_holdings,
            )
        )
        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.CENTRAL_BANK,
                agent_id=central.id,
                bond_holdings=central.bond_holdings,
            )
        )

        return {"updates": updates, "ledgers": ledgers, "transacted_quantity": qty}

    else:
        raise ValueError("side must be 'buy' or 'sell'")
