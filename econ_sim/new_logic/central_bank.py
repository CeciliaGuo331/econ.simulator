"""央行公开市场操作（最小化实现）。

功能：
- process_omo(world_state, tick, day, omo_ops)
  处理央行的 OMO 操作列表，每项形如 {"bond_id": str, "side": "buy"|"sell", "quantity": float, "price": float}

语义（简化）：
- buy: 央行从商业银行购买债券（若银行持有），向银行支付现金，央行增加其 bond_holdings；
- sell: 央行向商业银行出售债券（若央行持有），银行支付现金给央行，央行减少其 bond_holdings。

注意：这是简化的会计实现，用于在仿真中产生央行资产负债表变化和对银行流动性的直接影响。
"""

from __future__ import annotations

from typing import List, Dict, Any

from econ_sim.data_access import models


def process_omo(
    world_state: models.WorldState, tick: int, day: int, omo_ops: List[Dict[str, Any]]
):
    central = world_state.central_bank
    bank = world_state.bank
    updates = []
    ledgers = []

    if central is None:
        return (
            [],
            [],
            models.TickLogEntry(
                tick=tick, day=day, message="omo_skipped_no_central_bank"
            ),
        )

    for op in omo_ops:
        bond_id = op.get("bond_id")
        side = op.get("side")
        qty = float(op.get("quantity", 0.0))
        price = float(op.get("price", 0.0))
        if not bond_id or qty <= 0 or price <= 0:
            continue

        amount = qty * price

        if side == "buy":
            # central bank buys from commercial bank if available
            if bank is None:
                continue
            bank_holding = bank.bond_holdings.get(bond_id, 0.0)
            trade_qty = min(bank_holding, qty)
            if trade_qty <= 0:
                continue
            trade_amount = trade_qty * price
            # transfer cash from central to bank (central may go negative)
            central.balance_sheet.cash -= trade_amount
            bank.balance_sheet.cash += trade_amount
            # transfer bond ownership
            bank.bond_holdings[bond_id] = (
                bank.bond_holdings.get(bond_id, 0.0) - trade_qty
            )
            central.bond_holdings[bond_id] = (
                central.bond_holdings.get(bond_id, 0.0) + trade_qty
            )

            ledgers.append(
                models.LedgerEntry(
                    tick=tick,
                    day=day,
                    account_kind=models.AgentKind.CENTRAL_BANK,
                    entity_id=central.id,
                    entry_type="omo_buy",
                    amount=-trade_amount,
                    balance_after=central.balance_sheet.cash,
                    reference=bond_id,
                )
            )
            ledgers.append(
                models.LedgerEntry(
                    tick=tick,
                    day=day,
                    account_kind=models.AgentKind.BANK,
                    entity_id=bank.id,
                    entry_type="omo_sell_received",
                    amount=trade_amount,
                    balance_after=bank.balance_sheet.cash,
                    reference=bond_id,
                )
            )

            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.CENTRAL_BANK,
                    agent_id=central.id,
                    balance_sheet=central.balance_sheet.model_dump(),
                    bond_holdings=central.bond_holdings,
                )
            )
            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.BANK,
                    agent_id=bank.id,
                    balance_sheet=bank.balance_sheet.model_dump(),
                    bond_holdings=bank.bond_holdings,
                )
            )

        elif side == "sell":
            # central bank sells to commercial bank
            if bank is None:
                continue
            central_holding = central.bond_holdings.get(bond_id, 0.0)
            trade_qty = min(central_holding, qty)
            if trade_qty <= 0:
                continue
            trade_amount = trade_qty * price
            # transfer cash from bank to central
            bank.balance_sheet.cash -= trade_amount
            central.balance_sheet.cash += trade_amount
            # transfer bond ownership
            central.bond_holdings[bond_id] = (
                central.bond_holdings.get(bond_id, 0.0) - trade_qty
            )
            bank.bond_holdings[bond_id] = (
                bank.bond_holdings.get(bond_id, 0.0) + trade_qty
            )

            ledgers.append(
                models.LedgerEntry(
                    tick=tick,
                    day=day,
                    account_kind=models.AgentKind.CENTRAL_BANK,
                    entity_id=central.id,
                    entry_type="omo_sell",
                    amount=trade_amount,
                    balance_after=central.balance_sheet.cash,
                    reference=bond_id,
                )
            )
            ledgers.append(
                models.LedgerEntry(
                    tick=tick,
                    day=day,
                    account_kind=models.AgentKind.BANK,
                    entity_id=bank.id,
                    entry_type="omo_buy_paid",
                    amount=-trade_amount,
                    balance_after=bank.balance_sheet.cash,
                    reference=bond_id,
                )
            )

            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.CENTRAL_BANK,
                    agent_id=central.id,
                    balance_sheet=central.balance_sheet.model_dump(),
                    bond_holdings=central.bond_holdings,
                )
            )
            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.BANK,
                    agent_id=bank.id,
                    balance_sheet=bank.balance_sheet.model_dump(),
                    bond_holdings=bank.bond_holdings,
                )
            )

    log = models.TickLogEntry(
        tick=tick, day=day, message="omo_processed", context={"ops": len(ledgers)}
    )
    return updates, ledgers, log
