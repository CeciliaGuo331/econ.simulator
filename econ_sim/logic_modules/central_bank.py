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
import logging


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
            # transfer cash from central to bank via finance_market
            from . import finance_market

            try:
                t_updates, t_ledgers, t_log = finance_market.transfer(
                    world_state,
                    payer_kind=models.AgentKind.CENTRAL_BANK,
                    payer_id=central.id,
                    payee_kind=models.AgentKind.BANK,
                    payee_id=bank.id,
                    amount=trade_amount,
                    tick=tick,
                    day=day,
                )
                updates.extend(t_updates)
                ledgers.extend(t_ledgers)
            except Exception:
                # Do not perform direct balance mutations here. Log the error
                # so the issue can be diagnosed; keeping mutations confined to
                # finance_market preserves accounting invariants.
                logger = logging.getLogger(__name__)
                logger.exception(
                    "finance_market.transfer failed during OMO buy; cash transfer skipped"
                )
            # transfer bond ownership
            bank.bond_holdings[bond_id] = (
                bank.bond_holdings.get(bond_id, 0.0) - trade_qty
            )
            central.bond_holdings[bond_id] = (
                central.bond_holdings.get(bond_id, 0.0) + trade_qty
            )

            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.CENTRAL_BANK,
                    agent_id=central.id,
                    bond_holdings=central.bond_holdings,
                )
            )
            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.BANK,
                    agent_id=bank.id,
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
            # transfer cash from bank to central via finance_market
            from . import finance_market

            try:
                t_updates, t_ledgers, t_log = finance_market.transfer(
                    world_state,
                    payer_kind=models.AgentKind.BANK,
                    payer_id=bank.id,
                    payee_kind=models.AgentKind.CENTRAL_BANK,
                    payee_id=central.id,
                    amount=trade_amount,
                    tick=tick,
                    day=day,
                )
                updates.extend(t_updates)
                ledgers.extend(t_ledgers)
            except Exception:
                logger = logging.getLogger(__name__)
                logger.exception(
                    "finance_market.transfer failed during OMO sell; cash transfer skipped"
                )
            # transfer bond ownership
            central.bond_holdings[bond_id] = (
                central.bond_holdings.get(bond_id, 0.0) - trade_qty
            )
            bank.bond_holdings[bond_id] = (
                bank.bond_holdings.get(bond_id, 0.0) + trade_qty
            )

            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.CENTRAL_BANK,
                    agent_id=central.id,
                    bond_holdings=central.bond_holdings,
                )
            )
            updates.append(
                models.StateUpdateCommand.assign(
                    scope=models.AgentKind.BANK,
                    agent_id=bank.id,
                    bond_holdings=bank.bond_holdings,
                )
            )

    log = models.TickLogEntry(
        tick=tick, day=day, message="omo_processed", context={"ops": len(ledgers)}
    )
    return updates, ledgers, log
