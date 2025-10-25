"""简单的债券市场撮合与交易实现（最小化、可扩展）。

接口说明（最小化）：
- clear_bond_auction(world_state, bond, bids, tick, day)
  bids: list of dicts {"buyer_kind": AgentKind, "buyer_id": str|int, "price": float, "quantity": float}

返回值：{"updates": List[StateUpdateCommand], "ledgers": List[LedgerEntry], "trades": List[TradeRecord], "market_price": float, "market_yield": float}

该实现按出价从高到低匹配，按出价成交（价格优先）。
"""

from __future__ import annotations

from typing import List, Dict, Any
from uuid import uuid4
import statistics

from econ_sim.data_access import models
from econ_sim.data_access.models import (
    BondInstrument,
    StateUpdateCommand,
    LedgerEntry,
    TickLogEntry,
    TradeRecord,
    AgentKind,
)
from econ_sim.utils.settings import get_world_config
import math


def _apply_cash_delta(entity_balance_sheet, delta: float):
    bs = entity_balance_sheet
    bs.cash = bs.cash + delta
    return bs


def clear_bond_auction(
    world_state: models.WorldState,
    bond: BondInstrument,
    bids: List[Dict[str, Any]],
    tick: int,
    day: int,
):
    """按价格优先撮合债券发行（政府卖方）。

    bids: list of {buyer_kind, buyer_id, price, quantity}
    """
    # sort bids by price desc
    bids_sorted = sorted(bids, key=lambda b: b["price"], reverse=True)
    remaining = bond.outstanding
    trades: List[TradeRecord] = []
    ledgers: List[LedgerEntry] = []
    updates: List[StateUpdateCommand] = []
    traded_prices = []

    government = world_state.government

    for bid in bids_sorted:
        if remaining <= 0:
            break
        qty = min(bid["quantity"], remaining)
        price = bid["price"]
        amount = qty * price

        buyer_kind: AgentKind = bid["buyer_kind"]
        buyer_id = bid["buyer_id"]

        # route buyer object
        if buyer_kind == AgentKind.BANK and str(buyer_id) == str(world_state.bank.id):
            buyer = world_state.bank
        elif buyer_kind == AgentKind.HOUSEHOLD:
            buyer = world_state.households[int(buyer_id)]
        else:
            # for minimal impl, only support bank and household buyers
            continue

        # transfer cash from buyer to government
        buyer.balance_sheet.cash -= amount
        government.balance_sheet.cash += amount

        # assign bond holdings
        buyer.bond_holdings[bond.id] = buyer.bond_holdings.get(bond.id, 0.0) + qty
        bond.holders[str(buyer_id)] = bond.holders.get(str(buyer_id), 0.0) + qty
        government.debt_outstanding[bond.id] = (
            government.debt_outstanding.get(bond.id, 0.0) + qty
        )

        remaining -= qty
        traded_prices.append(price)

        trades.append(
            TradeRecord(
                tick=tick,
                day=day,
                buyer_kind=buyer_kind,
                buyer_id=str(buyer_id),
                seller_kind=AgentKind.GOVERNMENT,
                seller_id=government.id,
                quantity=qty,
                price=price,
                amount=amount,
            )
        )

        # ledger entries for buyer and government
        ledgers.append(
            LedgerEntry(
                tick=tick,
                day=day,
                account_kind=buyer_kind,
                entity_id=str(buyer_id),
                entry_type="bond_purchase",
                amount=-amount,
                balance_after=buyer.balance_sheet.cash,
                reference=bond.id,
            )
        )
        ledgers.append(
            LedgerEntry(
                tick=tick,
                day=day,
                account_kind=AgentKind.GOVERNMENT,
                entity_id=government.id,
                entry_type="bond_sale",
                amount=amount,
                balance_after=government.balance_sheet.cash,
                reference=bond.id,
            )
        )

        # create update commands for buyer and government
        if buyer_kind == AgentKind.BANK:
            updates.append(
                StateUpdateCommand.assign(
                    scope=AgentKind.BANK,
                    agent_id=buyer.id,
                    balance_sheet=buyer.balance_sheet.model_dump(),
                    bond_holdings=buyer.bond_holdings,
                )
            )
        else:
            updates.append(
                StateUpdateCommand.assign(
                    scope=AgentKind.HOUSEHOLD,
                    agent_id=buyer.id,
                    balance_sheet=buyer.balance_sheet.model_dump(),
                    bond_holdings=buyer.bond_holdings,
                )
            )

        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.GOVERNMENT,
                agent_id=government.id,
                balance_sheet=government.balance_sheet.model_dump(),
                debt_outstanding=government.debt_outstanding,
            )
        )

    # if any remaining (unsold), reduce outstanding to sold amount
    sold = bond.outstanding - remaining
    bond.outstanding = sold

    market_price = statistics.mean(traded_prices) if traded_prices else None
    market_yield = None
    if market_price and market_price > 0:
        # 计算更标准的到期收益率（YTM），若债券采用周期性 coupon，则按周期求解并年化
        try:
            # determine ticks per year
            cfg = get_world_config()
            ticks_per_year = int(cfg.simulation.ticks_per_day * 365)
        except Exception:
            ticks_per_year = 365

        # determine coupon schedule
        if getattr(bond, "coupon_frequency_ticks", 0):
            freq = int(bond.coupon_frequency_ticks)
            payments_per_year = max(1, int(ticks_per_year / max(1, freq)))
            n_periods = max(1, int(math.ceil((bond.maturity_tick - tick) / freq)))
            # coupon_rate is per-tick rate; coupon per period spanning `freq` ticks equals:
            coupon_per_period = bond.face_value * bond.coupon_rate * freq

            def pv_for_rate(annual_r: float) -> float:
                per_r = annual_r / payments_per_year
                pv = 0.0
                for t in range(1, n_periods + 1):
                    pv += coupon_per_period / ((1 + per_r) ** t)
                pv += bond.face_value / ((1 + per_r) ** n_periods)
                return pv

            # bisection search for annual_r
            low = -0.99
            high = 10.0
            pv_low = pv_for_rate(low)
            pv_high = pv_for_rate(high)
            target = market_price
            ytm = None
            # ensure we have root-bracketing
            if pv_low >= target and pv_high <= target:
                for _ in range(60):
                    mid = (low + high) / 2.0
                    pv_mid = pv_for_rate(mid)
                    if pv_mid > target:
                        low = mid
                    else:
                        high = mid
                ytm = (low + high) / 2.0
            market_yield = ytm
        else:
            # zero-coupon-like treated as single payment at maturity
            market_yield = None

    # register bond in government's debt_instruments registry
    government.debt_instruments[bond.id] = bond

    return {
        "updates": updates,
        "ledgers": ledgers,
        "trades": trades,
        "market_price": market_price,
        "market_yield": market_yield,
    }
